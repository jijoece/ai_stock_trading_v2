# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/14-apscheduler-tenacity-feasibility`
- Reviewed HEAD: `8722863703a0a4beac11a46242afe23fc4ba0821`
- Subject: PR 14 fix round 5: close annotated/nested-block/walrus reassignment and functools.partial decorator retry-guard bypasses
- Claude commits reviewed: 4f261aa81b77117c711bc77c4a4045f58863d444,76b399d1a270388425fb28884962b8a4c852ddf6,82860bcb28f730d983f1100cc3639fb092883f68,7b9d88eb4e2d56f354f3d60e84fc8eb898ebeb2d,b388928ce6fdcba8a5d6c165ab92460118e6600d,409244ca6c80e369f9207892a2e0f7743070823c,23bc0192a627bd5fa88c061995ff864ded94f502,8722863703a0a4beac11a46242afe23fc4ba0821
- Review scope: INCREMENTAL
- Fix round: 6
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: none
- Finding count: 0
- Fix commit: `ab8c755a0c1f4890e27a7abed9908ff812745222`

## Resolution

- Confirmed the finding's three cited bypasses against the reviewed-HEAD detector with synthetic
  modules before changing any code (same protocol as rounds 1-5):
  - The module-scope rebind scanner (`_rebind_offenders_in_block`) recursed into `if`/`try`/`with`
    blocks (closed in round 5) but not `for`/`async for`/`while` blocks, which are equally not a new
    scope in Python. A synthetic `_submit_checkpointed_attempt = broker_retry(
    _submit_checkpointed_attempt)` nested in a top-level `for` block returned no offenders.
  - The round-5 `functools.partial(retry, ...)`-wrapped decorator unwrap was written only for
    decorators; it did not also apply when a module-level reassignment's callee is itself the
    `functools.partial(...)` call (e.g. `_do_submit = functools.partial(retry, stop=3)(_do_submit)`
    on a transitively-called helper). A synthetic case of this form returned no offenders.
  - The third cited bypass -- an arbitrarily-named externally-defined wrapper -- is the documented,
    deliberately-accepted residual gap: `D12` in `docs/library-migration/DECISIONS.md` and the
    `_find_protected_function_offenders` docstring both record that distinguishing such a wrapper
    from an ordinary helper call would require inspecting an arbitrary external module, defeating
    this test's zero-dependency, always-runs design, and `test_detector_does_not_flag_an_arbitrarily_named_external_factory_call`
    already pins the accepted current behavior. Left unchanged; no further action needed.
- Fixed in `tests/unit/test_external_broker_no_tenacity_import_boundary.py`:
  - `_rebind_offenders_in_block` now also recurses into `ast.For`, `ast.AsyncFor`, and `ast.While`
    blocks (body and `orelse`), matching the existing recursion into `if`/`try`/`with`.
  - Factored the `functools.partial(retry, ...)` unwrap logic (previously inline in the decorator
    loop only) into one shared `_resolved_wrapper_name` helper, and reused it for both the decorator
    check and the reassignment-callee check in `_rebind_offender`, so a composed
    `functools.partial(retry, ...)(helper)` reassignment callee is unwrapped the same way a
    `functools.partial(retry, ...)` decorator already was.
- Added regression coverage (6 new tests, 39 total in the file):
  `test_detector_flags_a_module_level_reassignment_nested_in_a_for_block`,
  `test_detector_flags_a_module_level_reassignment_nested_in_a_while_block`,
  `test_detector_flags_a_module_level_reassignment_nested_in_an_async_for_block`,
  `test_detector_flags_a_composed_functools_partial_call_reassignment_of_a_transitively_called_helper`,
  and `test_detector_does_not_flag_an_unrelated_composed_functools_partial_call_reassignment` (negative
  case), plus the async-for positive case above.
- Validation passed: 33/33 focused tests in
  `tests/unit/test_external_broker_no_tenacity_import_boundary.py`; 99/99 across that file plus
  `test_lumibot_import_boundary.py`, `test_pr12_evaluation_docs.py`, `test_pr13_evaluation_docs.py`;
  and `nox -s ci` in full (`ci`: success; `tests`: 3216 passed/106 skipped; `paper_tests`: 160
  passed; `safety_typecheck`: 0 errors/0 warnings; `migration_smoke`: OK).

## Findings

None outstanding.

## Findings as reviewed (historical record)

### [P1] Investigate: structural guard permits retry wrappers around the broker-submission sink

Commit: `4f261aa81b77117c711bc77c4a4045f58863d444`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/tests/unit/test_external_broker_no_tenacity_import_boundary.py:260`

Concern: The guard may not satisfy `MASTER_PLAN.md` row 14’s requirement to prove that no `@retry`/`Retrying()` usage wraps the ambiguous broker-retry path. At reviewed HEAD, it intentionally permits externally defined retry wrappers with arbitrary names, and its module-scope rebinding scanner omits valid blocks such as `for`.

Evidence: Read-only synthetic AST diagnostics against the HEAD detector returned no offenders for all of these valid retry-wrapping forms:

- An arbitrarily named imported decorator on a helper transitively called by `retry_external_paper_order`.
- `_submit_checkpointed_attempt` rebound through `broker_retry(...)` inside a module-level `for` block.
- A transitively called helper rebound using `functools.partial(retry, ...)(helper)`.

The first bypass is explicitly accepted at lines 260–272 and pinned by `test_detector_does_not_flag_an_arbitrarily_named_external_factory_call`. The round-five recursive scanner at lines 354–364 handles only `if`, `try`, and `with`, while `_submit_checkpointed_attempt` contains the actual `runtime.submit_limit_order(...)` side effect at `/Users/jijopaul/workspace/ai_stock_trading_v2/src/trading_research/paper_books/external_broker.py:1296`. Later documentation acknowledges that this is merely a file-scoped syntactic check, but the authoritative master-plan row still requires structural proof that retry wrappers cannot wrap this path.

Potential impact if confirmed: A future Tenacity adoption could pass CI while automatically repeating an ambiguously completed broker submission, bypassing fresh authoritative `NOT_FOUND` evidence, explicit retry authorization, and retry limits. This could create duplicate external paper orders and inconsistent reservations or accounting state.

Investigation and conditional remediation: First verify the concern against the current code using the concrete bypasses above and confirm whether `MASTER_PLAN.md` row 14 still requires complete structural exclusion. Only if confirmed, replace or supplement the spelling-based source scan with an enforceable boundary around the broker-call sink, then add regression coverage for arbitrary imported wrappers, all module-scope control-flow rebindings, and composed wrapper factories. If the concern is disproved, the requirement has been formally narrowed, or another current invariant already prevents these forms, document that evidence and leave the code unchanged.

Validation: Confirm that every wrapper form capable of repeating `runtime.submit_limit_order(...)` fails the boundary test, while generic transport retries outside the ambiguous broker-submission path remain allowed. Run `nox -s tests -- tests/unit/test_external_broker_no_tenacity_import_boundary.py` and `nox -s ci`.

Tests or diagnostics run: Inspected every listed full commit diff chronologically and traced changes through `8722863703a0a4beac11a46242afe23fc4ba0821`; reviewed the relevant broker call path, migration decisions, master plan, ADR, and current test implementation. Ran read-only synthetic AST diagnostics demonstrating the three bypasses. Targeted pytest was attempted but could not start because the read-only environment had no writable temporary directory; no files were modified.
