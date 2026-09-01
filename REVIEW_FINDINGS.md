# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/14-apscheduler-tenacity-feasibility`
- Reviewed HEAD: `69f05a611ffd8d85e2e27543d60d76305ec6f8aa`
- Subject: Record PR 14 fix round 9: if/match-nested helper-def and function-local alias bypasses closed
- Claude commits reviewed: 4f261aa81b77117c711bc77c4a4045f58863d444,76b399d1a270388425fb28884962b8a4c852ddf6,82860bcb28f730d983f1100cc3639fb092883f68,7b9d88eb4e2d56f354f3d60e84fc8eb898ebeb2d,b388928ce6fdcba8a5d6c165ab92460118e6600d,409244ca6c80e369f9207892a2e0f7743070823c,23bc0192a627bd5fa88c061995ff864ded94f502,8722863703a0a4beac11a46242afe23fc4ba0821,ab8c755a0c1f4890e27a7abed9908ff812745222,8ab474fb0a67609d725b968d1660e874b393606e,4c2bead9a374390b34fe2c8482eafae1a695667b,6c62ba4632b53336ed735b0da5b77d123067c6da,e087fea72f15f8d4d9461b7c78f39ad99f3bb607,69f05a611ffd8d85e2e27543d60d76305ec6f8aa
- Review scope: FULL_PR
- Reviewed base: `c32021ef75174bc2271971626eb928fff83d1069`
- GitHub PR: #32
- Fix round: 9
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: none
- Finding count: 0
- Fix commit: `523d86e1f3ee5a1cc9e5b9be14c2840b0ff7bd6c`

## Resolution

Confirmed both findings against the reviewed-HEAD (`69f05a6`) detector with the exact synthetic
modules given in each finding before changing any code (same protocol as rounds 1-9):

- **Finding 1** (`[P1]` branch-dependent module aliases can bypass the broker retry guard) was a
  **confirmed bypass**: `_resolve_import_aliases` folded the `if`/`else` reproduction's
  `wrapper = retry` / `wrapper = ordinary` into one unconditional `dict[str, str]`, so the `else`
  assignment silently overwrote the `if` branch's retry binding even though the retry-wrapped path
  remains reachable whenever `enabled` is true. The exact reproduction from the finding returned
  `[]` from `_find_protected_function_offenders` against reviewed HEAD, not the expected offender.
- **Finding 2** (`[P1]` incomplete function-local alias analysis can hide retry-decorated helpers)
  was a **confirmed bypass** on both counts: `_local_aliases_in_block` matched only a bare,
  single-target, unannotated `ast.Assign`, so the annotated-alias reproduction
  (`submit: object = _do_submit`) was invisible to it; and, like Finding 1, it also folded
  mutually exclusive branch bindings into one unconditional dict, so the branch-dependent
  reproduction (`if enabled: submit = _do_submit / else: submit = ordinary`) resolved only to the
  `else` branch's non-retry alias. Both synthetic modules from the finding returned `[]` against
  reviewed HEAD, not the expected offender.

Both findings share the same root cause: every prior round's alias resolution (module-scope
`_resolve_import_aliases`, round 7; function-local `_local_aliases_in_block`, round 9) threaded a
single-value `dict[str, str]` through the statements it visited, which is correct for a
straight-line reassignment (a real overwrite) but incorrect for mutually exclusive branches (both
bindings are feasible at runtime, and only one executes on any given call). Fixed by replacing both
resolvers' assignment-chaining logic with a shared `_accumulate_name_bindings` dataflow pass: each
name now resolves to a `frozenset` of every value it could feasibly hold, computed by evaluating
each branch of `if`/`else`, each `try`/`except`/`else`, each `match` `case`, and the
entered-or-not-entered paths of `for`/`while` independently from the same incoming state, then
joining the results via `_merge_binding_states` (set union per name) instead of letting one
branch's ending state overwrite another's. A straight-line reassignment within one block still
replaces the state exactly as Python does, so unrelated existing behavior (e.g. round-4/5/6/8
reassignment-offender detection, which reasons about literal overwrite at module scope) is
unaffected. The same pass also now recognizes `ast.AnnAssign` at both module and function-local
scope, closing Finding 2's annotated-alias gap directly. Every downstream consumer of the alias map
(`_resolved_call_names`, `_resolved_wrapper_names`, `_direct_local_calls`) now resolves against the
full feasible set rather than a single name, so a call or decorator reachable through *any* branch
of an ambiguous alias is flagged, and `_direct_local_calls` adds a call-graph edge to *every*
feasible callee of a branch-dependent local alias.

Fix commit `523d86e1f3ee5a1cc9e5b9be14c2840b0ff7bd6c` closes both confirmed bypasses and adds six
regression tests: two positive module-scope branch-dependent-alias cases (retry binding on the
`if` branch, then on the `else` branch, proving the fix does not simply prefer whichever branch is
visited first), one negative control (neither branch's module-scope alias is retry-shaped), one
positive annotated function-local-alias case, one positive branch-dependent function-local-alias
case, and one negative control (neither branch's local alias is retry-decorated).

Validation: confirmed both bypasses against the pre-fix detector with the findings' own synthetic
modules before changing any code. Post-fix,
`.venv/bin/python -m pytest tests/unit/test_external_broker_no_tenacity_import_boundary.py -q`
passed 54/54 (48 pre-existing plus 6 new regression tests). `.venv/bin/nox -s ci` (`tests`
[3237 passed, 106 skipped], `paper_tests` [160 passed], `safety_typecheck` [0 errors],
`migration_smoke`) passed in full against this exact working tree.

## Findings (as reviewed)

### [P1] Investigate: branch-dependent module aliases can bypass the broker retry guard

Commit: `4c2bead9a374390b34fe2c8482eafae1a695667b`

Location: [test_external_broker_no_tenacity_import_boundary.py](/Users/jijopaul/workspace/ai_stock_trading_v2/tests/unit/test_external_broker_no_tenacity_import_boundary.py:257)

Concern: The module-scope alias resolver may collapse mutually exclusive control-flow assignments into whichever assignment appears last in AST traversal. A retry alias that is active on another reachable branch can therefore be hidden.

Evidence: `_resolve_import_aliases` builds one unconditional `dict[str, str]` and sequentially visits both the `if` body and `else` body. Against reviewed HEAD, this valid synthetic module returned no offenders:

```python
from retry_utils import retry

if enabled:
    wrapper = retry
else:
    wrapper = ordinary

@wrapper
def _do_submit():
    pass

def retry_external_paper_order():
    _do_submit()
```

The `else` assignment overwrites `wrapper -> retry` with `wrapper -> ordinary`, even though the retry-wrapped path remains executable when `enabled` is true. This is a same-file source bypass, not one of the documented external-module or runtime limitations.

Potential impact if confirmed: A feature-, platform-, or version-dependent retry wrapper could reach `_submit_checkpointed_attempt` while CI passes. An ambiguous broker response might then be automatically resubmitted without fresh authoritative `NOT_FOUND` evidence or explicit authorization, potentially producing duplicate external paper orders and inconsistent reservations or accounting state.

Investigation and conditional remediation: First verify the concern against the current code using the concrete mutually exclusive branch reproduction. Only if confirmed, make alias analysis conservatively preserve every feasible retry-shaped binding—or fail closed when a protected-path alias has conflicting branch bindings—and add regression coverage. If another current invariant rejects the construction, or the concern is disproved or already fixed, document that evidence and leave the code unchanged.

Validation: Add positive cases with the retry binding in either branch and negative cases where all feasible bindings are non-retry helpers. Run:

```text
nox -s tests -- tests/unit/test_external_broker_no_tenacity_import_boundary.py
nox -s ci
```

### [P1] Investigate: incomplete function-local alias analysis can hide retry-decorated helpers

Commit: `e087fea72f15f8d4d9461b7c78f39ad99f3bb607`

Location: [test_external_broker_no_tenacity_import_boundary.py](/Users/jijopaul/workspace/ai_stock_trading_v2/tests/unit/test_external_broker_no_tenacity_import_boundary.py:326)

Concern: `_local_aliases_in_block` recognizes only a single-target, unannotated `ast.Assign`, and—like the module resolver—reduces all control-flow paths to one final binding. Valid local aliases can therefore leave retry-decorated helpers absent from the reachable call graph.

Evidence: Both of these synthetic modules returned no offenders against reviewed HEAD:

```python
from retry_utils import retry

@retry
def _do_submit():
    pass

def retry_external_paper_order():
    submit: object = _do_submit
    submit()
```

```python
from retry_utils import retry

@retry
def _do_submit():
    pass

def ordinary():
    pass

def retry_external_paper_order():
    if enabled:
        submit = _do_submit
    else:
        submit = ordinary
    submit()
```

The first alias is an `ast.AnnAssign`, which the resolver ignores. In the second case, the later `else` binding overwrites the reachable retry binding. In both cases `_do_submit` is excluded from the transitive helper set, so its `@retry` decorator is never inspected.

Potential impact if confirmed: A type-annotated or conditional local refactor in a protected broker function could silently disable the intended structural boundary, allowing automatic retry behavior around an ambiguous external submission.

Investigation and conditional remediation: First verify both reproductions against the current detector. Only if confirmed, extend local alias analysis to cover relevant assignment forms and conservatively merge feasible control-flow bindings; then add regression coverage for annotations and mutually exclusive branches. If existing enforcement makes either construction impossible, or the concern is disproved or already fixed, document that evidence and leave the code unchanged.

Validation: Add positive fixtures for annotated aliases and both branch orderings, plus negative controls for ordinary helpers and aliases in nested function/class scopes. Run the targeted Nox test session followed by `nox -s ci`.

Tests or diagnostics run: Inspected the specified commits chronologically, their full changes, the final detector, the broker submission sink, D12, PR 14 evaluation, matrices, status, and relevant test configuration. Read-only synthetic AST diagnostics reproduced all three fail-open cases above (`[]`). `nox` was unavailable on this runner, and `pytest` was not installed, so canonical test sessions could not be executed. `git diff --check` was also run; it reported only whitespace in `REVIEW_FINDINGS.md`, which is outside the requested finding categories. No files were modified.
