# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/14-apscheduler-tenacity-feasibility`
- Reviewed HEAD: `7f53240d254e0961e9808051161f82871e63d3f3`
- Subject: Record PR 14 fix round 12: program-order, composed-partial, and function-local Retrying-alias bypasses closed
- Claude commits reviewed: 4f261aa81b77117c711bc77c4a4045f58863d444,76b399d1a270388425fb28884962b8a4c852ddf6,82860bcb28f730d983f1100cc3639fb092883f68,7b9d88eb4e2d56f354f3d60e84fc8eb898ebeb2d,b388928ce6fdcba8a5d6c165ab92460118e6600d,409244ca6c80e369f9207892a2e0f7743070823c,23bc0192a627bd5fa88c061995ff864ded94f502,8722863703a0a4beac11a46242afe23fc4ba0821,ab8c755a0c1f4890e27a7abed9908ff812745222,8ab474fb0a67609d725b968d1660e874b393606e,4c2bead9a374390b34fe2c8482eafae1a695667b,6c62ba4632b53336ed735b0da5b77d123067c6da,e087fea72f15f8d4d9461b7c78f39ad99f3bb607,69f05a611ffd8d85e2e27543d60d76305ec6f8aa,7e523012072b6887e6bb4d9de61158e6df49d648,5840263d87fd53bf4561d8c444bc2135871435bd,6958cc72250572f16df50ac6ad5dfc6937fd9c3c,7f53240d254e0961e9808051161f82871e63d3f3
- Review scope: FULL_PR
- Reviewed base: `c32021ef75174bc2271971626eb928fff83d1069`
- GitHub PR: #32
- Fix round: 12
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: none
- Finding count: 0
- Fix commit: `c355b37863fa6a536fd6391d7048be3c6de46a18`

## Resolution

Confirmed all four findings against the reviewed-HEAD (`7f53240`) detector with the exact (or
directly equivalent) synthetic reproductions given in each finding before changing any code (same
protocol as rounds 1-12):

1. A module-scope alias bound to `retry` before a decorator use, then rebound to an ordinary
   callable afterward (`wrapper = retry; @wrapper def _do_submit(): pass; wrapper = ordinary`),
   returned `[]` from `_find_protected_function_offenders` instead of flagging the decorator.
2. A same-named `import ... as ...` inside an unrelated function's own body (`from retry_utils
   import retry` at module scope, then `from ordinary_utils import ordinary as retry` inside an
   unrelated `def`) returned `[]` instead of flagging the module-scope `@retry` decorator it
   silently overwrote.
3. A `Retrying()` call hidden in a protected function's own default argument (`def
   retry_external_paper_order(runner=Retrying()): ...`) returned `[]` from either detector.
4. The scope-blind-import variant of finding 2 (`from retry_utils import retry as wrapper` used as
   a decorator, later shadowed only inside an unrelated function's own `from ordinary import
   ordinary as wrapper`) returned `[]`.

Root causes and fixes:

1 and 4. The decorator scan in `_find_protected_function_offenders` resolved every decorator
   against `_resolve_import_aliases`'s single, whole-module *final* alias state, which reflects
   only each name's last assignment anywhere in the file -- correct semantics for a call inside a
   function body (Python resolves that free variable at call time, after the whole module has
   finished loading), but wrong for a decorator, which evaluates immediately when its `def`
   statement executes. A new `_decorator_alias_states` function records, for every module-scope
   function definition, the alias state accumulated from only the statements that textually
   precede it (via a new `capture` parameter threaded through `_accumulate_name_bindings`), and the
   decorator scan now resolves each function's decorators against that per-definition state instead
   of the whole-module final one.
2 and 4 (import-scope half). `_resolve_import_aliases`'s import-alias seed walked the *entire* tree
   (`ast.walk(tree)`), so an import inside an unrelated function or class body -- a real, separate
   scope this file's other alias and rebind scans have always excluded -- was recorded as if it
   were a module-scope binding. A new `_import_only_aliases` function (extracted from
   `_resolve_import_aliases`) restricts collection to `_module_scope_statements`, the same scope
   boundary already enforced for assignment aliases.
3. Neither the decorator scan (which only inspected `decorator_list`) nor the inner-call scan
   (which only walks `node.body`) ever looked at `node.args.defaults`/`kw_defaults`, both of which
   evaluate immediately at `def`-time exactly like a decorator. The decorator scan now also walks
   each default value (positional and keyword-only), resolved against the same per-definition alias
   state introduced for findings 1 and 4.

Fix commit `c355b37863fa6a536fd6391d7048be3c6de46a18` closes all four confirmed bypasses and adds
eight regression tests: positive/negative pairs for the program-order and import-scope root causes,
plus positional and keyword-only default coverage sharing one negative case.

Validation: confirmed all four bypasses against the pre-fix detector with the findings' own
synthetic reproductions before changing any code. Post-fix,
`.venv/bin/python -m pytest tests/unit/test_external_broker_no_tenacity_import_boundary.py -q`
passed 75/75 (67 pre-existing plus 8 new regression tests). `nox -s ci` (`tests` [3258 passed, 106
skipped], `paper_tests` [160 passed], `safety_typecheck` [0 errors], `migration_smoke`) passed in
full against this exact working tree.

## Findings (as reviewed)

### [P1] Investigate: module alias resolution ignores decorator-time program order

Commit: 4c2bead9a374390b34fe2c8482eafae1a695667b

Location: tests/unit/test_external_broker_no_tenacity_import_boundary.py:371-385,445-479

Concern: The module-scope alias analysis may use only a name’s final binding, allowing a retry wrapper used earlier as a decorator on a submission helper to escape the structural safety guard.

Evidence: Against reviewed HEAD, this synthetic sequence returned no offenders:

```python
from retry_utils import retry

wrapper = retry

@wrapper
def _do_submit():
    pass

wrapper = ordinary

def retry_external_paper_order():
    _do_submit()
```

Python evaluates `@wrapper` when `_do_submit` is defined, while `_resolve_import_aliases()` reduces `wrapper` to its later final binding, `ordinary`. The round-12 documentation explicitly preserves final-binding semantics for module aliases even though decorators can execute before a later reassignment.

Potential impact if confirmed: A future change could retry the broker-submission path automatically while the CI guard passes. An ambiguous submission could consequently be repeated, risking duplicate external paper orders and violating the operator-controlled retry boundary.

Investigation and conditional remediation: First verify the concern against the current detector using the exact program-order reproduction above. If confirmed, make module-scope decorator and wrapper resolution account for the binding feasible at each use site, or conservatively retain earlier retry-shaped bindings, and add positive and negative regression coverage. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Add a test proving that an alias bound to `retry` before a reachable helper’s decorator remains prohibited even if the alias is rebound afterward, plus a negative case where the alias is rebound before the decorator and the decorator therefore receives only an ordinary callable. Run the targeted test through `nox -s tests -- tests/unit/test_external_broker_no_tenacity_import_boundary.py`.

**Resolution:** Confirmed. The decorator scan now resolves against `_decorator_alias_states`'s
per-definition alias state (the state accumulated from only the statements preceding the function's
own `def`) instead of `_resolve_import_aliases`'s whole-module final state. See fix commit
`c355b37863fa6a536fd6391d7048be3c6de46a18` and regression tests
`test_detector_flags_a_retry_decorated_helper_despite_a_later_module_scope_alias_rebind` /
`test_detector_does_not_flag_a_decorator_fed_by_an_alias_rebound_before_it`.

### [P1] Investigate: function-local imports can corrupt module alias resolution

Commit: 82860bcb28f730d983f1100cc3639fb092883f68

Location: tests/unit/test_external_broker_no_tenacity_import_boundary.py:469-479

Concern: `_resolve_import_aliases()` gathers import aliases with `ast.walk(tree)`, including imports inside unrelated functions and classes, and treats them as module-scope aliases.

Evidence: Against reviewed HEAD, this synthetic module returned no offenders:

```python
from retry_utils import retry

def unrelated():
    from ordinary_utils import ordinary as retry

@retry
def _do_submit():
    pass

def retry_external_paper_order():
    _do_submit()
```

The nested import incorrectly records module-level `retry` as resolving to `ordinary`, masking the reachable helper’s retry decorator. The detector returned `[]` and its resolved alias state was `{'retry': frozenset({'ordinary'})}`.

Potential impact if confirmed: An unrelated local import can silently disable enforcement for a retry-decorated broker helper elsewhere in the module. CI could then approve automatic retries around ambiguous external-paper submissions, with duplicate-order risk.

Investigation and conditional remediation: First verify the concern against the current detector using the exact nested-import reproduction above. If confirmed, restrict initial import-alias collection to true module-scope statements using the same scope boundary applied to assignment aliases, and add regression coverage. If disproved or already fixed, document the evidence and leave the code unchanged.

Validation: Add a positive test showing that a function- or class-local alias cannot overwrite a module-level retry name, plus negative coverage for legitimate module-scope import aliases. Run the targeted test through `nox -s tests -- tests/unit/test_external_broker_no_tenacity_import_boundary.py`.

**Resolution:** Confirmed. `_resolve_import_aliases`'s import-alias seed is now built by the new
`_import_only_aliases`, which restricts collection to `_module_scope_statements` instead of
`ast.walk(tree)`, the same scope boundary already enforced for assignment aliases. See fix commit
`c355b37863fa6a536fd6391d7048be3c6de46a18` and regression tests
`test_detector_flags_a_retry_decorated_helper_despite_an_unrelated_function_local_import_alias` /
`test_detector_does_not_flag_a_module_scope_alias_shadowed_only_by_a_function_local_import`.

Tests or diagnostics run:

- Reviewed all 18 requested commit diffs chronologically and checked later range changes for resolution.
- Executed both synthetic AST reproductions directly against the reviewed-HEAD detector; both returned no offenders.
- Targeted pytest was attempted but could not initialize because the read-only environment had no usable temporary directory.
- The canonical Nox command could not be run because `nox` is not installed in the environment.
- No files were modified.

### [P2] Investigate: Investigate retry constructors in default arguments

Commit: `5840263d87fd53bf4561d8c444bc2135871435bd`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/tests/unit/test_external_broker_no_tenacity_import_boundary.py:656`

Concern: An active GitHub review thread raises the following potentially valid issue:

> **<sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub>  Investigate retry constructors in default arguments**
> 
> Investigate whether calls in function defaults also need scanning: this walk starts at `node.body`, so parsing `from retry_utils import Retrying; def retry_external_paper_order(runner=Retrying()): return runner(_submit_checkpointed_attempt)` returns no offenders from either detector, even though the `Retrying` instance can execute and automatically retry the submission helper. If this form is introduced, CI would pass while an ambiguous broker submission could be repeated; verify with that synthetic fixture and, if confirmed, scan positional/keyword defaults and add regression coverage, otherwise record the enforced invariant excluding it.
> 
> AGENTS.md reference: [AGENTS.md:L60-L62](https://github.com/jijoece/ai_stock_trading_v2/blob/5840263d87fd53bf4561d8c444bc2135871435bd/AGENTS.md#L60-L62)
> 
> Useful? React with 👍 / 👎.

Evidence: [chatgpt-codex-connector review thread](https://github.com/jijoece/ai_stock_trading_v2/pull/32#discussion_r3899620798) is current, unresolved, and not outdated.

Potential impact if confirmed: Merging would carry the reported defect into main.

Investigation and conditional remediation: Verify the comment against the current code and reproduce the behavior where practical. If confirmed, fix it and add regression coverage. If it is invalid or already fixed, document the evidence and do not make an unnecessary code change.

Validation: Run the focused regression test and the repository's canonical validation; a subsequent full-PR review must find no remaining defect.

**Resolution:** Confirmed. The decorator/wrapper-call scan now also walks
`node.args.defaults`/`kw_defaults` (both evaluate immediately at `def`-time, exactly like a
decorator), resolved against the same per-definition alias state introduced for the program-order
finding above. See fix commit `c355b37863fa6a536fd6391d7048be3c6de46a18` and regression tests
`test_detector_flags_a_retry_constructor_in_a_protected_functions_positional_default` /
`test_detector_flags_a_retry_constructor_in_a_protected_functions_keyword_only_default` /
`test_detector_does_not_flag_an_ordinary_factory_call_in_a_protected_functions_default`.

### [P2] Investigate: Investigate scope-blind import alias resolution

Commit: `7f53240d254e0961e9808051161f82871e63d3f3`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/tests/unit/test_external_broker_no_tenacity_import_boundary.py:474`

Concern: An active GitHub review thread raises the following potentially valid issue:

> **<sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub>  Investigate scope-blind import alias resolution**
> 
> Investigate whether walking the entire tree here lets function-local imports overwrite unrelated module bindings: a synthetic module with `from retry_utils import retry as wrapper`, `@wrapper` on a helper called by `retry_external_paper_order`, and a later unrelated function containing `from ordinary import ordinary as wrapper` makes `_find_protected_function_offenders` return `[]`, because the local import replaces the module alias in this global map even though Python scopes keep them separate. If confirmed, this can let a retry-wrapped broker submission pass the structural boundary and repeat an ambiguous order; restrict alias collection by scope or resolve bindings at use sites, and add this fixture as regression coverage.
> 
> AGENTS.md reference: [AGENTS.md:L60-L62](https://github.com/jijoece/ai_stock_trading_v2/blob/7f53240d254e0961e9808051161f82871e63d3f3/AGENTS.md#L60-L62)
> 
> Useful? React with 👍 / 👎.

Evidence: [chatgpt-codex-connector review thread](https://github.com/jijoece/ai_stock_trading_v2/pull/32#discussion_r3899724186) is current, unresolved, and not outdated.

Potential impact if confirmed: Merging would carry the reported defect into main.

Investigation and conditional remediation: Verify the comment against the current code and reproduce the behavior where practical. If confirmed, fix it and add regression coverage. If it is invalid or already fixed, document the evidence and do not make an unnecessary code change.

Validation: Run the focused regression test and the repository's canonical validation; a subsequent full-PR review must find no remaining defect.

**Resolution:** Confirmed -- this is the same root cause as "function-local imports can corrupt
module alias resolution" above. `_import_only_aliases` restricting collection to
`_module_scope_statements` closes both. See fix commit `c355b37863fa6a536fd6391d7048be3c6de46a18`
and regression tests
`test_detector_flags_a_retry_decorated_helper_despite_an_unrelated_function_local_import_alias` /
`test_detector_does_not_flag_a_module_scope_alias_shadowed_only_by_a_function_local_import`.

