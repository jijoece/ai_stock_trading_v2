# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/18-final-authority-safety-audit`
- Reviewed HEAD: `333efbd4dd5b388598ffb936335302742495a31a`
- Subject: PR 18 fix round 13: guard handler installation outside logging_config
- Reviewed base: `bd95d2c7ba14a5333f1128ecf7b34a2059a95602`
- Review scope: FULL_PR
- GitHub PR: #36
- Trigger: manual Claude Code review (`/code-review 36`)
- Review status: RESOLVED in fix round 14 (finding 7 deferred)
- Highest priority: P2
- Finding count: 10
- Note: line numbers refer to `333efbd`, the PR head on GitHub. The local
  branch was at `8399913`, one commit behind, when this review was written.

The previous round's findings (rounds 1–12, through fix commit `932395a`)
were all resolved. This file replaces that record.

## Findings

### [P2] 1. Refresh CLI returns config-loader text instead of the sanitized error

Location: `src/trading_research/cli.py:426`

Concern: The external-paper refresh command now calls
`_load_config_or_error(config_path)` outside the `try`/`except`. Before this
PR, `load_paper_books_config()` ran inside the `try`, so any config failure
went through `_sanitized_cli_error` and came back as the generic code and
message. Now a `PaperBooksConfigError` comes back as its own text: the
absolute path plus the underlying `OSError` or YAML parser message
(`paper_books/config.py:575-578`). Any other exception the loader raises
now propagates instead of being sanitized. The error value also changes from
a dict (`{code, message}`) to a plain string.

Failure scenario: An unreadable or malformed `--paper-books-config` (or
default config) returns
`{"error": "cannot read paper-books config at /abs/path/...: [Errno 13] Permission denied"}`
or the YAML parser's text, which can quote file contents. A consumer that
reads `outcome["error"]["code"]` breaks.

Context: `paper_book_list_cli` and other paper-books commands already
return `PaperBooksConfigError` text this way, so this may be the intended
convention for this domain error. Decide which applies. If the error should
stay sanitized, move the load back inside the `try` or route the error
through `_sanitized_cli_error`. If returning domain text is intended,
document it beside the Part 17 rule and accept the change in shape.

Validation: Add a regression test for refresh with an unreadable override
and with invalid YAML, asserting the error shape and that no raw OS or parser
text appears.

### [P2] 2. Redaction of cyclic containers recurses without bound

Location: `src/trading_research/logging_config.py:151`

Concern: `_MAX_STRUCTLOG_HOOK_DEPTH` limits only nested `__structlog__` hook
calls. Recursion into mappings (line 151), sequences and sets (line 156), and
the threadlocal wrapped-dict unwrap (line 161) passes `_hook_depth`
through unchanged and does no cycle detection.

Failure scenario: An `extra` value, or a `__structlog__` hook result, that is
a dict containing itself (`d["self"] = d`) makes `_redact_value` raise
`RecursionError` inside the formatter. `logging.Handler.handleError` then
drops the record and prints the record's `msg`/`args` to stderr without
redaction, which gets around the module's no-leak contract.

Remediation: Track visited container `id()`s, or cap total recursion depth,
and emit a type-only placeholder on a cycle. Add a regression test with a
self-referencing dict and a hook that returns one.

### [P3] 3. A class object passed as an extra renders as "hook failed"

Location: `src/trading_research/logging_config.py:171`

Concern: `getattr(value, "__structlog__", None)` on a class returns the
unbound function. Calling it raises `TypeError`, which the code catches and
turns into `<structlog hook failed: type>`. structlog's own fallback only
skips the hook on `AttributeError` and otherwise lets the class serialize as
its repr.

Failure scenario: `logger.info("x", extra={"model": SomeHookedClass})` logs a
placeholder instead of the class repr, losing diagnostic information.

Remediation: Skip the hook when `value` is a type (`isinstance(value, type)`),
or bind the lookup to the instance only.

### [P3] 4. Set and frozenset extras now log as JSON arrays

Location: `src/trading_research/logging_config.py:160`

Concern: Sets and frozensets are now rebuilt as a `tuple` (the fix for the
earlier unhashable-hook finding). Before this PR they reached the renderer
as a repr string. The JSON log format therefore changes for those fields.

Failure scenario: `extra={"symbols": {"AAPL", "MSFT"}}` used to log
`"symbols": "{'AAPL', 'MSFT'}"`. It now logs `"symbols": ["MSFT", "AAPL"]`,
in no fixed order. Log parsers or tests keyed on the old string form break.

Remediation: Accept the change and record it in D16, or sort the items when
they are orderable so the output is deterministic.

### [P3] 5. `parse_status` takes the declaration from a block that also holds history

Location: `scripts/migration_helper.py:172`

Concern: Everything before the first `## ` heading is treated as the
declaration block. In the current `STATUS.md` that block also contains
history prose ("PR 17 ... is merged", "This file's 'Current phase'..."). It
parses correctly only because the real declaration happens to come first.

Failure scenario: A future header that recaps an older line such as
"Next phase: PR 18" before "**Next phase: none.**" makes `_NEXT_PHASE.search`
return the stale phase. A `STATUS.md` whose declaration sits under a
`## Current` heading parses as `(None, None)`.

Remediation: Anchor on the bold declaration lines (`**Current phase: ...**`,
`**Next phase: ...**`) or require exactly one match, and fail on ambiguity.

### [P3] 6. Extras are redacted and repr'd even in plain-text mode

Location: `src/trading_research/logging_config.py:189`

Concern: `_redact_event_dict` runs every extra through `_redact_value` on
every log call. That calls `__structlog__` hooks and `repr()`, even under
`configure_logging(json_output=False)` (the default), where `_render_plain`
throws the extras away.

Failure scenario: `extra={"frame": large_dataframe}` pays for a full
DataFrame repr on every call and runs any hook with side effects, while
nothing is rendered.

Remediation: Do object and hook redaction only on the JSON path. Finding 7
would achieve this.

### [P3] 7. The redactor re-implements structlog's JSON fallback

Location: `src/trading_research/logging_config.py:161`

Concern: The threadlocal wrapped-dict, `__structlog__`, and repr branches
copy `structlog.processors._json_fallback_handler` inside the generic
redactor. Doing so needs the private `_ThreadLocalDictWrapper` via
`sys.modules`, a hand-rolled depth counter, and sentinel logic. The copy
already differs from structlog (see finding 3).

Remediation: Keep `_redact_value` for plain data, and do object handling in
`JSONRenderer(default=lambda o: _redact_value(_json_fallback_handler(o)))` (or
an equivalent wrapper). Redaction would then happen only where serialization
happens, which also addresses findings 3 and 6.

### [P3] 8. Refresh duplicates the config-load prologue, including a dead branch

Location: `src/trading_research/cli.py:427`

Concern: The refresh function copies `_external_paper_cli`'s config-loading
code. Its `or paper_books_config is None` check can never be true:
`_load_config_or_error` always returns either a config or an error, never
`(None, None)`. The fallback message is fabricated for that unreachable case.

Remediation: Extract one helper that loads the config or returns an error
dict, and use it from both the read-only path and the runtime path. Resolve
together with finding 1.

### [P3] 9. Reintroduction guards parse every source file many times

Location: `tests/unit/test_removed_implementation_reintroduction_guards.py:127`

Concern: `_tree(path)` is called inside the loop over alternative-calendar
modules, so each file under `src/` and `scripts/` is parsed once per module
(5 times in that test), and each guard test parses the tree again. That is
about 10 or more parses per file across the suite.

Remediation: Move `tree = _tree(path)` out of the inner loop, or build one
cached path-to-tree map per module (e.g. `functools.cache`).

### [P3] 10. Reintroduction guards miss dynamic imports and `Handler.format` overrides

Location: `tests/unit/test_removed_implementation_reintroduction_guards.py:62`

Concern: The guards see only static `import`/`from` statements and classes
whose bases are named like Formatters.

Failure scenario: `importlib.import_module("structlog")`,
`__import__("holidays")`, or
`class H(logging.StreamHandler): def format(self, r): ...` adds a second
logging or calendar authority, and every guard test still passes.

Remediation: Also flag `importlib.import_module`/`__import__` calls whose
argument is a string literal naming a guarded module, and `Handler`
subclasses that define `format`. Otherwise, narrow the D16 invariant's
wording to static imports.

## Tests or diagnostics run

None. This was a static review of the PR diff. No `nox` sessions were run.

## Resolution (fix round 14)

| # | Outcome |
| - | ------- |
| 1 | Fixed. `_load_external_config` sends unexpected loader exceptions through `_sanitized_cli_error`; `PaperBooksConfigError` stays domain text, as in `paper-book-list` (D16 Ruling 6). |
| 2 | Fixed. Cycle and 64-level nesting cutoff with a type-only placeholder. |
| 3 | Fixed. Class objects skip the instance hook. |
| 4 | Accepted and sorted when orderable; recorded in D16 Ruling 5. |
| 5 | Fixed. Line-anchored declarations; conflicting ones raise `HelperError`. |
| 6 | Fixed. Plain-text mode redacts strings only. |
| 7 | Deferred. A `JSONRenderer(default=...)` refactor would replace behavior pinned over thirteen rounds; findings 3 and 6 were fixed directly. Recorded in D16 Ruling 5. |
| 8 | Fixed with finding 1 (shared helper, dead branch removed). |
| 9 | Fixed. Parsing cached by file content. |
| 10 | Fixed. Literal dynamic imports and `Handler.format` overrides are reported. |
