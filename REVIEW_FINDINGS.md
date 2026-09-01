# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/14-apscheduler-tenacity-feasibility`
- Reviewed HEAD: `b1aee8eace90ff5f994bedb69c7a65d7812bfcab`
- Subject: Record PR 14 fix round 10: branch-dependent alias-collapse bypasses closed
- Claude commits reviewed: 4f261aa81b77117c711bc77c4a4045f58863d444,76b399d1a270388425fb28884962b8a4c852ddf6,82860bcb28f730d983f1100cc3639fb092883f68,7b9d88eb4e2d56f354f3d60e84fc8eb898ebeb2d,b388928ce6fdcba8a5d6c165ab92460118e6600d,409244ca6c80e369f9207892a2e0f7743070823c,23bc0192a627bd5fa88c061995ff864ded94f502,8722863703a0a4beac11a46242afe23fc4ba0821,ab8c755a0c1f4890e27a7abed9908ff812745222,8ab474fb0a67609d725b968d1660e874b393606e,4c2bead9a374390b34fe2c8482eafae1a695667b,6c62ba4632b53336ed735b0da5b77d123067c6da,e087fea72f15f8d4d9461b7c78f39ad99f3bb607,69f05a611ffd8d85e2e27543d60d76305ec6f8aa
- Review scope: FULL_PR
- Reviewed base: `c32021ef75174bc2271971626eb928fff83d1069`
- GitHub PR: #32
- Fix round: 11
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: none
- Finding count: 0
- Fix commit: `7e523012072b6887e6bb4d9de61158e6df49d648`

## Resolution

Confirmed the finding against the reviewed-HEAD (`b1aee8e`) detector with the exact synthetic
reproductions given in the finding before changing any code (same protocol as rounds 1-10): a
function-local conditional-expression alias (`submit = _do_submit if enabled else ordinary`) and a
chained assignment (`first = submit = _do_submit`), both followed by `submit()` inside a protected
function, returned `[]` from `_find_protected_function_offenders` against reviewed HEAD instead of
the expected offender.

Root cause: `_accumulate_name_bindings`'s assignment branch only matched a single-target
`ast.Assign`/`ast.AnnAssign` whose value was a bare `ast.Name`, so a conditional-expression value
was ignored entirely (neither arm was recorded, leaving the target unbound) and a multi-target
assignment left every target unbound. Fixed by adding `_resolve_value_names`, which recurses
through `ast.Name` and `ast.IfExp` (unioning both arms, since either is a feasible runtime value at
the call site) and returns `None` for any other expression shape (leaving that assignment
unresolved, as before), and widening the assignment branch to bind every `ast.Name` target of a
multi-target `ast.Assign` to that resolved value, matching Python's own chained-assignment
semantics. Both `_resolve_import_aliases` (module scope) and `_local_aliases_in_block`
(function-local scope) share this fix, since both are built on `_accumulate_name_bindings`.

Fix commit `7e523012072b6887e6bb4d9de61158e6df49d648` closes the confirmed bypass and adds eight
regression tests: positive/negative pairs for a conditional-expression alias and a chained alias,
each at both module scope and function-local scope.

Validation: confirmed the bypass against the pre-fix detector with the finding's own synthetic
modules before changing any code. Post-fix,
`.venv/bin/python -m pytest tests/unit/test_external_broker_no_tenacity_import_boundary.py -q`
passed 62/62 (54 pre-existing plus 8 new regression tests). `nox -s ci` (`tests` [3245 passed, 106
skipped], `paper_tests` [160 passed], `safety_typecheck` [0 errors], `migration_smoke`) passed in
full against this exact working tree.

## Findings (as reviewed)

### [P1] Investigate: conditional aliases bypass the broker retry boundary

**Commit:** 82860bcb28f730d983f1100cc3639fb092883f68

**Location:** [test_external_broker_no_tenacity_import_boundary.py](/Users/jijopaul/workspace/ai_stock_trading_v2/tests/unit/test_external_broker_no_tenacity_import_boundary.py:293)

**Concern:** The transitive-helper guard introduced by this commit may still allow a retry-decorated helper to reach a protected broker-submission function through a conditional or chained local alias.

**Evidence:** `_accumulate_name_bindings` recognizes only single-target `Name = Name` assignments. A synthetic protected function using `submit = _do_submit if enabled else ordinary; submit()`—where `_do_submit` has `@retry`—produced no offenders. A chained alias, `first = submit = _do_submit; submit()`, also produced no offenders. Both execute the retry-decorated helper on feasible paths, but the call graph does not mark it reachable.

**Potential impact if confirmed:** A future change could automatically retry an ambiguous broker submission while the structural safety test continues to pass, risking duplicate external paper orders or bypassing the fresh-evidence retry gate.

**Investigation and conditional remediation:** First verify the concern against the current detector with synthetic conditional-expression and chained-assignment cases. If confirmed, extend alias resolution to conservatively retain every feasible local-function binding from these assignment forms and add regression coverage demonstrating that the retry-decorated helper is detected. If the concern is disproved or already fixed through another invariant, document that evidence and leave the code unchanged.

**Validation:** Confirm the new cases fail before remediation and report the retry decorator afterward; also retain negative cases proving equivalent aliases to ordinary helpers remain allowed.

Tests or diagnostics run:

- Reviewed every specified Claude-authored commit’s full diff chronologically and checked later changes through HEAD `b1aee8eace90ff5f994bedb69c7a65d7812bfcab`.
- Direct AST diagnostic: conditional and chained alias reproductions both returned `[]`.
- Targeted pytest invocation could not start because the read-only environment had no writable temporary directory; no tests executed.
- No files were modified and no broker, scheduler, model, or external service was accessed.
