# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/15-structlog-migration`
- Reviewed HEAD: `37f1ac5f8e7efca00ea3cc06c2c623c8e0d82fbb`
- Subject: PR 15: preserve plain-text exception diagnostics
- Claude commits reviewed: 13d49ad3370eb9563ad0960cb0568086db6d87d5,30fdc546be223173e754ef637a2d0c1436b5dd5e,88d3cb55a5b4667e2e34fe3c1d89f57bc5c87672
- Review scope: FULL_PR
- Reviewed base: `33dce8fa06d6bb3cad5b879f826348f221347e49`
- GitHub PR: #33
- Fix round: 2
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: none
- Finding count: 0
- Fix commit: pending (working tree only; not yet committed)

## Resolution

Reproduced all five findings against the reviewed-HEAD (`37f1ac5f`) implementation with the
exact synthetic cases each finding gave, before changing any code. All five confirmed:

1. Registering `nested"secret` and logging `extra={"context": {"value": 'nested"secret'}}` in
   JSON mode produced `{"context": {"value": "nested\"secret"}, ...}` -- the registered plaintext
   no longer appeared as a contiguous substring after `JSONRenderer` escaped the quote, so the
   post-render `redact()` safeguard (`_render_json`) could not find it (findings 1, 2, 4 -- the
   same root cause, reported three times against slightly different symptoms and commits).
2. Logging `extra={"account_number": "123456789"}` in JSON mode produced the account number
   verbatim at the top level of the rendered line: `_redact_event_dict` only pattern-matches
   `api_key`/`token`/`secret`/`password`/`authorization`-shaped key=value text, and an account
   number matches none of those patterns (finding 3). The pre-migration `JsonRedactingFormatter`
   never exposed `account_number` at all, because it only ever serialized an 8-field allowlist
   (`run_id`, `workstream_id`, `batch_id`, `custom_id`, `operation`, `status`, `duration_ms`,
   `error_type`); `ExtraAdder()` removed that allowlist, and nothing replaced its protection for
   fields, like an account number, that carry no secret-shaped pattern.
3. In the AST-based retry-guard test detector
   (`tests/unit/test_external_broker_no_tenacity_import_boundary.py`), a synthetic module with
   `alias = helper` (retry-decorated), then `def retry_external_paper_order(fn=alias): fn()`,
   then `alias = ordinary` produced `[]` from `_find_protected_function_offenders` -- no offender
   -- even though `fn` is bound to the retry-decorated `helper` at `def`-time and invoked
   (finding 5). `_parameter_default_aliases` resolved the default expression against the
   whole-module *final* alias state instead of the alias state as of that function's own `def`
   statement, so the later, unrelated `alias = ordinary` silently erased the binding that
   actually applied.

Root causes and fixes:

1 & 2 (`src/trading_research/logging_config.py`): `_redact_event_dict` only ever redacted
   top-level string values, so `ExtraAdder`-surfaced nested mappings/sequences reached
   `JSONRenderer` unredacted, and the existing post-render safety net (`_render_json`) redacts
   the *serialized* line, after JSON escaping has already broken the verbatim substring match
   `redact()` depends on. `_redact_event_dict` now delegates to a new `_redact_value`, which
   redacts strings and recurses into mappings/sequences -- on the raw Python objects, before
   `JSONRenderer` serializes and escapes them -- so escaping can no longer defeat the match. A new
   `_is_sensitive_key`/`_SENSITIVE_KEY_SUBSTRINGS` check redacts a value by key name alone
   (`key`, `token`, `secret`, `password`, `authorization`, `account`) wherever it appears, at any
   nesting depth and regardless of the value's type, restoring the protection the old 8-field
   allowlist gave account numbers and similar fields that carry no secret-shaped pattern; `account`
   was also added to `_SECRET_PATTERNS`' key-name alternation for the free-text/message case. The
   existing post-render `redact()` pass in `_render_json` is left in place as a secondary,
   defense-in-depth boundary.
3 (`tests/unit/test_external_broker_no_tenacity_import_boundary.py`): `_direct_local_calls` and
   `_transitively_called_local_helpers` now take an additional `definition_aliases` parameter --
   the caller's per-definition alias snapshot from `_decorator_alias_states` (the same one
   `_find_protected_function_offenders` already uses for a function's own decorators and
   default-argument retry-shaped calls) -- and `_parameter_default_aliases` resolves against it
   instead of the whole-module final `aliases`. Body-call resolution (`_local_aliases_in_block`)
   is unchanged and still resolves against the final state, which remains correct: a call inside a
   function body only ever executes after the whole module has finished loading, but a default
   value binds once, immediately, at the `def` statement's own program point -- the same semantics
   already established for decorators.

Fix commit (pending, working tree only) closes all three confirmed root causes and adds eight
regression tests: `test_json_output_redacts_registered_secret_in_nested_extra_field_with_json_escaped_characters`
(parametrized over a quote, a backslash, and a newline), `test_json_output_redacts_account_number_extra_field_by_key`,
`test_json_output_redacts_nested_non_string_sensitive_field_by_key`,
`test_json_output_redacts_sensitive_keys_inside_list_extra_field`,
`test_json_output_does_not_redact_unrelated_extra_fields` (negative case, guards against
overbroad key-name matching) in `tests/unit/test_logging_config.py`, and
`test_detector_flags_a_default_bound_helper_via_an_alias_reassigned_after_definition` in
`tests/unit/test_external_broker_no_tenacity_import_boundary.py`.

Validation: reproduced all three root causes against the pre-fix code with each finding's own
synthetic reproduction before changing any code (see above). Post-fix,
`.venv/bin/python -m pytest tests/unit/test_logging_config.py
tests/unit/test_external_broker_no_tenacity_import_boundary.py -q` passed 108/108 (100 pre-existing
plus 8 new regression tests). `nox -s ci` (`tests` [3291 passed, 106 skipped], `paper_tests` [160
passed], `safety_typecheck` [0 errors], `migration_smoke`) passed in full against this exact
working tree.

## Findings (as reviewed)

### [P1] Investigate: JSON escaping can bypass nested-secret redaction

Commit: 13d49ad3370eb9563ad0960cb0568086db6d87d5

Location: src/trading_research/logging_config.py:70-74,110-122

Concern: Verify whether registered secrets containing JSON-escaped characters can leak when nested inside an `extra` value. The migration exposes arbitrary extras through `ExtraAdder`, but only redacts top-level strings before serialization; the later rendered-line safeguard operates after JSON escaping.

Evidence: Against reviewed HEAD, registering `nested"secret` and logging `extra={"context": {"value": 'nested"secret'}}` produced:

```json
{"context": {"value": "nested\"secret"}, ...}
```

The registered plaintext no longer appears as a contiguous substring after `JSONRenderer` escapes the quote, so `_render_json()` cannot replace it. The regression test added by `30fdc546be223173e754ef637a2d0c1436b5dd5e` covers only a secret without characters requiring JSON escaping. Before `13d49ad`, unknown nested extras were not included in JSON output; `ExtraAdder` introduced that exposure.

Potential impact if confirmed: Credentials containing quotes, backslashes, newlines, or other JSON-escaped characters could be written to structured logs despite `register_secret()` and the module's no-secret logging invariant.

Investigation and conditional remediation: First reproduce the escaped nested-secret case against the current code and verify the decoded JSON value still contains the registered secret. Only if confirmed should redaction be applied recursively to mappings and sequences before serialization, with careful handling of non-container objects, and regression coverage added. If another enforced invariant prevents such values from reaching nested extras, document that evidence and leave the code unchanged.

Validation: Add JSON tests for registered nested secrets containing quotes, backslashes, and control characters; assert both that the decoded field is redacted and that no reconstructable credential remains in the rendered line. Run `nox -s tests -- tests/unit/test_logging_config.py`, followed by `nox -s ci` if the concern is confirmed and fixed.

**Resolution:** Confirmed against reviewed HEAD with the exact reproduction above. `_redact_event_dict`
now recurses into nested mappings/sequences via `_redact_value` and redacts each raw string value
*before* `JSONRenderer` serializes and escapes it, closing the gap for quotes, backslashes, and
control characters alike. See fix (pending commit) and regression test
`test_json_output_redacts_registered_secret_in_nested_extra_field_with_json_escaped_characters`.

### [P1] Investigate: Redact nested extra values before JSON rendering

Commit: `13d49ad3370eb9563ad0960cb0568086db6d87d5`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/src/trading_research/logging_config.py:72`

Concern: An active GitHub review thread raises the following potentially valid issue:

> **<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)</sub></sub>  Redact nested extra values before JSON rendering**
> 
> Investigate whether structured `extra` values can contain credentials: `_redact_event_dict` only calls `redact()` on top-level strings, while `ExtraAdder` forwards mappings/lists that `JSONRenderer` later serializes. In JSON mode, `extra={"operation": {"token": "abcd1234efgh"}}` can therefore emit the raw token; the predecessor scrubbed the final serialized JSON, including non-string values under its supported extra fields. This would violate the module's centralized no-secret logging guarantee. Verify with an end-to-end JSON capture using a nested secret and, if confirmed, fix the rendering boundary or recursively redact values and add regression coverage.
> 
> Useful? React with 👍 / 👎.

Evidence: [chatgpt-codex-connector review thread](https://github.com/jijoece/ai_stock_trading_v2/pull/33#discussion_r4000916014) is current, unresolved, and not outdated.

Potential impact if confirmed: Merging would carry the reported defect into main.

Investigation and conditional remediation: Verify the comment against the current code and reproduce the behavior where practical. If confirmed, fix it and add regression coverage. If it is invalid or already fixed, document the evidence and do not make an unnecessary code change.

Validation: Run the focused regression test and the repository's canonical validation; a subsequent full-PR review must find no remaining defect.

**Resolution:** Confirmed -- same root cause as the finding above (this is the same gap reported
via the GitHub review thread). Closed by the same fix: `_redact_value` recurses into nested
mappings/sequences before serialization. See regression test
`test_json_output_redacts_registered_secret_in_nested_extra_field_with_json_escaped_characters`
and the pre-existing `test_json_output_redacts_registered_secret_in_nested_extra_field`.

### [P1] Investigate: Investigate account identifiers exposed by ExtraAdder

Commit: `88d3cb55a5b4667e2e34fe3c1d89f57bc5c87672`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/src/trading_research/logging_config.py:102`

Concern: An active GitHub review thread raises the following potentially valid issue:

> **<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)</sub></sub>  Investigate account identifiers exposed by ExtraAdder**
> 
> Investigate whether broadening JSON output to every `LogRecord` extra field exposes account identifiers: the predecessor copied only eight allowlisted fields, but `ExtraAdder` now serializes `extra={"account_number": "123456789"}`, and neither `_SECRET_PATTERNS` nor final-line redaction recognizes that value. Unlike the previously addressed nested-token case, this unregistered account number reaches the JSON line verbatim despite the module's explicit no-account-number contract; verify with an end-to-end capture and, if confirmed, restore a safe allowlist or add key-aware redaction plus regression coverage.
> 
> AGENTS.md reference: [AGENTS.md:L58-L66](https://github.com/jijoece/ai_stock_trading_v2/blob/88d3cb55a5b4667e2e34fe3c1d89f57bc5c87672/AGENTS.md#L58-L66)
> 
> Useful? React with 👍 / 👎.

Evidence: [chatgpt-codex-connector review thread](https://github.com/jijoece/ai_stock_trading_v2/pull/33#discussion_r4000945248) is current, unresolved, and not outdated.

Potential impact if confirmed: Merging would carry the reported defect into main.

Investigation and conditional remediation: Verify the comment against the current code and reproduce the behavior where practical. If confirmed, fix it and add regression coverage. If it is invalid or already fixed, document the evidence and do not make an unnecessary code change.

Validation: Run the focused regression test and the repository's canonical validation; a subsequent full-PR review must find no remaining defect.

**Resolution:** Confirmed against reviewed HEAD: `extra={"account_number": "123456789"}` reached
the JSON line verbatim as a top-level field. The AGENTS.md line reference in the review comment is
stale (the file's current "Code Review Rules" section does not name account numbers explicitly),
but the module's own docstring already states the same invariant ("No API key, bearer token,
OAuth token, account number, or raw credential header may reach a log line"), and the
pre-migration `JsonRedactingFormatter`'s 8-field allowlist bears this out: `account_number` was
never one of the eight fields it copied, so it never reached a log line before this migration.
Added key-aware redaction (`_is_sensitive_key`/`_SENSITIVE_KEY_SUBSTRINGS`, matching `key`,
`token`, `secret`, `password`, `authorization`, `account`) that redacts a value by its key name
alone, at any nesting depth and regardless of type, restoring that protection without
reintroducing a rigid allowlist that would silently drop other legitimate extra fields. See
regression tests `test_json_output_redacts_account_number_extra_field_by_key`,
`test_json_output_redacts_nested_non_string_sensitive_field_by_key`,
`test_json_output_redacts_sensitive_keys_inside_list_extra_field`, and the negative case
`test_json_output_does_not_redact_unrelated_extra_fields`.

### [P1] Investigate: Investigate JSON escaping before redacting nested secrets

Commit: `88d3cb55a5b4667e2e34fe3c1d89f57bc5c87672`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/src/trading_research/logging_config.py:95`

Concern: An active GitHub review thread raises the following potentially valid issue:

> **<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)</sub></sub>  Investigate JSON escaping before redacting nested secrets**
> 
> Investigate registered secrets containing JSON-escaped characters: for a nested value registered as `abc"remaining-secret`, `JSONRenderer` emits `abc\"remaining-secret` before this call, so verbatim replacement no longer finds it; under a `password` key, the regex instead redacts only the prefix and can leave the remainder while producing invalid JSON. This escaped-value reproduction is fresh evidence beyond the prior simple nested-token finding; verify quote, backslash, and newline cases end to end and, if confirmed, recursively redact raw values before serialization and add regression coverage.
> 
> AGENTS.md reference: [AGENTS.md:L58-L66](https://github.com/jijoece/ai_stock_trading_v2/blob/88d3cb55a5b4667e2e34fe3c1d89f57bc5c87672/AGENTS.md#L58-L66)
> 
> Useful? React with 👍 / 👎.

Evidence: [chatgpt-codex-connector review thread](https://github.com/jijoece/ai_stock_trading_v2/pull/33#discussion_r4000945254) is current, unresolved, and not outdated.

Potential impact if confirmed: Merging would carry the reported defect into main.

Investigation and conditional remediation: Verify the comment against the current code and reproduce the behavior where practical. If confirmed, fix it and add regression coverage. If it is invalid or already fixed, document the evidence and do not make an unnecessary code change.

Validation: Run the focused regression test and the repository's canonical validation; a subsequent full-PR review must find no remaining defect.

**Resolution:** Confirmed -- same root cause as the first finding above, reproduced end to end with
quote, backslash, and newline cases. Closed by the same recursive, pre-serialization
`_redact_value` fix. See the parametrized regression test
`test_json_output_redacts_registered_secret_in_nested_extra_field_with_json_escaped_characters`.

### [P1] Investigate: Investigate default aliases resolved after their definition

Commit: `88d3cb55a5b4667e2e34fe3c1d89f57bc5c87672`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/tests/unit/test_external_broker_no_tenacity_import_boundary.py:714`

Concern: An active GitHub review thread raises the following potentially valid issue:

> **<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)</sub></sub>  Investigate default aliases resolved after their definition**
> 
> Investigate whether default-argument aliases are resolved against the wrong program point: with `alias = helper`, then `def retry_external_paper_order(fn=alias): fn()`, followed by `alias = ordinary`, this call receives the whole-module final alias state and reports no offender even when `helper` is `@retry`-decorated. Python captured `helper` when the function was defined, so the structural guard can miss an automatic retry around the ambiguous submission path; verify this synthetic case and, if confirmed, resolve defaults against the existing per-definition alias state and add regression coverage.
> 
> AGENTS.md reference: [AGENTS.md:L58-L62](https://github.com/jijoece/ai_stock_trading_v2/blob/88d3cb55a5b4667e2e34fe3c1d89f57bc5c87672/AGENTS.md#L58-L62)
> 
> Useful? React with 👍 / 👎.

Evidence: [chatgpt-codex-connector review thread](https://github.com/jijoece/ai_stock_trading_v2/pull/33#discussion_r4000945257) is current, unresolved, and not outdated.

Potential impact if confirmed: Merging would carry the reported defect into main.

Investigation and conditional remediation: Verify the comment against the current code and reproduce the behavior where practical. If confirmed, fix it and add regression coverage. If it is invalid or already fixed, document the evidence and do not make an unnecessary code change.

Validation: Run the focused regression test and the repository's canonical validation; a subsequent full-PR review must find no remaining defect.

**Resolution:** Confirmed with exactly the synthetic reproduction given: `_find_protected_function_offenders`
returned `[]` against the pre-fix detector. The module already had the correct machinery for this
-- `_decorator_alias_states` builds a per-definition alias snapshot, and
`_find_protected_function_offenders` already used it for a function's own decorators and
default-argument retry-shaped calls -- but `_transitively_called_local_helpers` and
`_direct_local_calls` still resolved `_parameter_default_aliases` against the whole-module
*final* `aliases` state instead. Both now take a `definition_aliases` parameter populated from
`_decorator_alias_states`, so a default-bound helper resolves against the alias state as of its
own `def` statement, matching decorator semantics. See fix (pending commit) and regression test
`test_detector_flags_a_default_bound_helper_via_an_alias_reassigned_after_definition`.

Tests or diagnostics run:

- Reproduced all three root causes against reviewed HEAD (`37f1ac5f`) with each finding's own
  synthetic case, using `.venv/bin/python`, before making any code change.
- `.venv/bin/python -m pytest tests/unit/test_logging_config.py
  tests/unit/test_external_broker_no_tenacity_import_boundary.py -q`: 108/108 passed (100
  pre-existing plus 8 new regression tests) after the fix.
- `nox -s ci`: `tests` [3291 passed, 106 skipped], `paper_tests` [160 passed], `safety_typecheck`
  [0 errors, 0 warnings], `migration_smoke` [OK] -- all five sessions successful.
