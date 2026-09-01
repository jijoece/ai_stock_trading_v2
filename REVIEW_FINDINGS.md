# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/14-apscheduler-tenacity-feasibility`
- Reviewed HEAD: `5840263d87fd53bf4561d8c444bc2135871435bd`
- Subject: Record PR 14 fix round 11: conditional-expression and chained-alias bypasses closed
- Claude commits reviewed: 4f261aa81b77117c711bc77c4a4045f58863d444,76b399d1a270388425fb28884962b8a4c852ddf6,82860bcb28f730d983f1100cc3639fb092883f68,7b9d88eb4e2d56f354f3d60e84fc8eb898ebeb2d,b388928ce6fdcba8a5d6c165ab92460118e6600d,409244ca6c80e369f9207892a2e0f7743070823c,23bc0192a627bd5fa88c061995ff864ded94f502,8722863703a0a4beac11a46242afe23fc4ba0821,ab8c755a0c1f4890e27a7abed9908ff812745222,8ab474fb0a67609d725b968d1660e874b393606e,4c2bead9a374390b34fe2c8482eafae1a695667b,6c62ba4632b53336ed735b0da5b77d123067c6da,e087fea72f15f8d4d9461b7c78f39ad99f3bb607,69f05a611ffd8d85e2e27543d60d76305ec6f8aa,7e523012072b6887e6bb4d9de61158e6df49d648,5840263d87fd53bf4561d8c444bc2135871435bd
- Review scope: FULL_PR
- Reviewed base: `c32021ef75174bc2271971626eb928fff83d1069`
- GitHub PR: #32
- Fix round: 12
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: none
- Finding count: 0
- Fix commit: `6958cc72250572f16df50ac6ad5dfc6937fd9c3c`

## Resolution

Confirmed all three findings against the reviewed-HEAD (`5840263`) detector with the exact (or
directly equivalent) synthetic reproductions given in each finding before changing any code (same
protocol as rounds 1-11):

1. A function-local alias reassigned *after* the call that used its earlier, retry-decorated
   binding (`submit = _do_submit; submit(); submit = ordinary`, `_do_submit` carrying `@retry`,
   all inside `retry_external_paper_order`) returned `[]` from
   `_find_protected_function_offenders` instead of flagging the decorator.
2. A composed `functools.partial(retry, stop=3)(_do_submit)()` invocation inside a protected
   function returned `[]` instead of flagging the call.
3. `broker_retrying = Retrying` (imported from `retry_utils`) followed by
   `with broker_retrying(): ...`, both inside a protected function, returned `[]` instead of
   flagging the call.

Root causes and fixes:

1. `_local_aliases_in_block` fed `_direct_local_calls` a single *end-of-function* alias state
   (`_accumulate_name_bindings`'s straight-line overwrite semantics), so a harmless local
   reassignment after the call discarded the earlier binding before any call in the function was
   resolved against it. `_accumulate_name_bindings` gained a `monotonic` flag: in monotonic mode a
   straight-line reassignment unions with, rather than replaces, a name's prior binding, matching
   how mutually exclusive branches already merge (round 10). `_local_aliases_in_block` now passes
   `monotonic=True`, so a local alias's earlier value stays resolvable at every call site in the
   function regardless of a later rebind. `_resolve_import_aliases` (module scope) keeps the
   default, non-monotonic (real-overwrite) semantics, since only a name's final module-scope
   binding is ever consulted there — decorators and reassignment callees both resolve at
   module-def time, not at some earlier call site.
2. `_resolved_wrapper_names`'s `functools.partial(...)` unwrap only ever peeled one level, and the
   inner-call scan resolved a call's callee through the non-partial-aware `_resolved_call_names`
   besides, so a composed invocation whose own callee is itself an `ast.Call` matched neither
   `retry` nor `Retrying`. `_resolved_wrapper_names` now recurses when a call's callee is itself an
   `ast.Call`, unwrapping arbitrarily deep partial-composition chains one call at a time.
3. The inner-call scan in `_find_protected_function_offenders` resolved every callee through the
   *module-scope* `aliases` chain only, never through a same-named alias assigned inside the
   calling function's own body — the identical function-local-alias gap round 9 closed for
   `_direct_local_calls`'s call-graph edges, but never shared with this scan of a function's own
   executable calls. It now resolves against `_local_aliases_in_block`'s per-function state
   (already layered on top of `aliases`) instead of the bare module-scope `aliases`, closing
   findings 2 and 3 together. The decorator scan is untouched: a decorator always evaluates in the
   enclosing module scope, never the decorated function's own local scope.

Fix commit `6958cc72250572f16df50ac6ad5dfc6937fd9c3c` closes all three confirmed bypasses and adds
six regression tests: positive/negative pairs for each finding.

Validation: confirmed all three bypasses against the pre-fix detector with the findings' own
synthetic modules before changing any code. Post-fix,
`.venv/bin/python -m pytest tests/unit/test_external_broker_no_tenacity_import_boundary.py -q`
passed 68/68 (62 pre-existing plus 6 new regression tests). `nox -s ci` (`tests` [3251 passed, 106
skipped], `paper_tests` [160 passed], `safety_typecheck` [0 errors], `migration_smoke`) passed in
full against this exact working tree. `scripts/check_links.sh` passed (187 OK, 0 errors).

## Findings (as reviewed)

### [P1] Investigate: later local reassignment hides an earlier protected helper call

**Commit:** `e087fea72f15f8d4d9461b7c78f39ad99f3bb607`

**Location:** `tests/unit/test_external_broker_no_tenacity_import_boundary.py:523-528`

**Concern:** The function-local alias analysis appears to resolve every call using the alias state
at the end of the function. A harmless reassignment after a broker-path call can therefore erase
the earlier binding and make a retry-wrapped helper unreachable to the detector.

**Evidence:** `_local_aliases_in_block(node.body, aliases)` computed one final state before
`ast.walk(node)` examined any calls. The synthetic protected path from the finding — `submit =
_do_submit; submit(); submit = ordinary`, with `_do_submit` carrying `@retry` — returned `[]` from
the detector at reviewed HEAD, although `submit()` invokes the retry-decorated helper before the
overwrite.

**Resolution:** Confirmed. `_accumulate_name_bindings` gained a `monotonic` flag that
`_local_aliases_in_block` now passes; a straight-line reassignment unions with the prior binding
instead of replacing it, so the earlier value remains resolvable at every call site regardless of
a later rebind. See fix commit `6958cc72250572f16df50ac6ad5dfc6937fd9c3c` and regression tests
`test_detector_flags_a_retry_decorated_helper_called_before_a_later_local_alias_overwrite` /
`test_detector_does_not_flag_a_local_alias_overwrite_when_neither_binding_is_retry_decorated`.

### [P1] Investigate: direct `functools.partial(retry, …)` invocation bypasses the call scan

**Commit:** `8722863703a0a4beac11a46242afe23fc4ba0821`

**Location:** `tests/unit/test_external_broker_no_tenacity_import_boundary.py:655-659`

**Concern:** Partial-wrapper unwrapping is used for decorators and module-level rebindings, but
calls inside protected or reachable helper bodies use `_resolved_call_names` directly. A composed
`functools.partial(retry, …)` invocation is consequently not recognized.

**Evidence:** The finding's executable retry wrapper —
`functools.partial(retry, stop=3)(_do_submit)()` inside `retry_external_paper_order` — produced no
offenders at reviewed HEAD. The outer call's callee is an `ast.Call`, which `_resolved_call_names`
cannot resolve; the inner call resolves only to `partial`. `_resolved_wrapper_names`, which
understands this composition, was not used by the body-call scan.

**Resolution:** Confirmed. `_resolved_wrapper_names` now recurses when a call's callee is itself an
`ast.Call`, unwrapping arbitrarily deep partial-composition chains, and the inner-call scan now
calls `_resolved_wrapper_names` on the whole `ast.Call` node instead of `_resolved_call_names` on
just its `.func`. See fix commit `6958cc72250572f16df50ac6ad5dfc6937fd9c3c` and regression tests
`test_detector_flags_a_composed_functools_partial_call_invocation_inside_a_protected_function` /
`test_detector_does_not_flag_an_unrelated_composed_functools_partial_call_invocation`.

### [P2] Investigate: Investigate function-local retry API aliases

Commit: `b1aee8eace90ff5f994bedb69c7a65d7812bfcab`

Location: `tests/unit/test_external_broker_no_tenacity_import_boundary.py:617`

Concern: An active GitHub review thread raised the following potentially valid issue: this call
scan resolves `inner.func` only against module-scope `aliases`; parsing `from retry_utils import
Retrying; def retry_external_paper_order(): broker_retrying = Retrying; with broker_retrying():
pass` returns no offenders. If `Retrying` is Tenacity-backed, this directly wraps the protected
path and may repeat an ambiguous broker submission while the guard passes.

Evidence: [chatgpt-codex-connector review thread](https://github.com/jijoece/ai_stock_trading_v2/pull/32#discussion_r3899586225).
Reproduced the exact fixture from the thread against reviewed HEAD: `[]`, no offenders, although
`broker_retrying()` invokes the `Retrying` context manager.

**Resolution:** Confirmed. The inner-call scan previously resolved every callee through the
module-scope `aliases` chain only, never through a same-named alias assigned inside the calling
function's own body. It now resolves against `_local_aliases_in_block`'s per-function alias state
instead, matching how `_direct_local_calls` already resolves call-graph edges (round 9). See fix
commit `6958cc72250572f16df50ac6ad5dfc6937fd9c3c` and regression tests
`test_detector_flags_a_retrying_context_manager_call_through_a_function_local_alias` /
`test_detector_does_not_flag_a_function_local_alias_call_of_a_non_retry_context_manager`.
