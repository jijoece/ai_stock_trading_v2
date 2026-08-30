# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/14-apscheduler-tenacity-feasibility`
- Reviewed HEAD: `6c62ba4632b53336ed735b0da5b77d123067c6da`
- Subject: Record PR 14 fix round 7: module-scope name-to-name assignment-aliasing bypass closed
- Claude commits reviewed: 4f261aa81b77117c711bc77c4a4045f58863d444,76b399d1a270388425fb28884962b8a4c852ddf6,82860bcb28f730d983f1100cc3639fb092883f68,7b9d88eb4e2d56f354f3d60e84fc8eb898ebeb2d,b388928ce6fdcba8a5d6c165ab92460118e6600d,409244ca6c80e369f9207892a2e0f7743070823c,23bc0192a627bd5fa88c061995ff864ded94f502,8722863703a0a4beac11a46242afe23fc4ba0821,ab8c755a0c1f4890e27a7abed9908ff812745222,8ab474fb0a67609d725b968d1660e874b393606e,4c2bead9a374390b34fe2c8482eafae1a695667b,6c62ba4632b53336ed735b0da5b77d123067c6da
- Review scope: FULL_PR
- Reviewed base: `c32021ef75174bc2271971626eb928fff83d1069`
- GitHub PR: #32
- Fix round: 2
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: none
- Finding count: 0
- Fix commit: `b0c501568e8404e7ec8bdd930018621a8110ab93`

## Resolution

Confirmed each of the seven findings against the reviewed-HEAD (`6c62ba4`) detector with
synthetic modules before changing any code (same protocol as rounds 1-7):

- **Finding 1** (`[P1]` retry wrappers can still bypass the broker-submission guard) bundled
  three concrete reproductions. The annotated-alias case
  (`broker_retry: object = retry` then `@broker_retry` on a protected function) was already
  closed by earlier rounds -- reproduced and confirmed flagged, not `[]`. The other two were
  **confirmed bypasses**: a module-level reassignment of a protected name to a retry-wrapper
  call nested inside a `match` statement's `case` body, and the same reassignment nested inside
  an `except*` (PEP 654) handler, both returned `[]`. Fixed by recursing
  `_rebind_offenders_in_block` and `_resolve_import_aliases`'s `_module_scope_statements` helper
  into `ast.Match` case bodies and `ast.TryStar` handlers (alongside the existing `ast.Try`
  handling), matching the same non-scope-introducing-block treatment already given to
  `if`/`try`/`with`/`for`/`while`. `ast.TryStar` is guarded with `hasattr(ast, "TryStar")` since
  this project's floor is Python 3.10 and `except*` only parses on 3.11+; the new regression test
  for it is skipped below 3.11. The "arbitrary externally-named wrapper" portion remains the
  pre-existing, deliberately accepted residual gap documented in
  `_find_protected_function_offenders`'s docstring and pinned by
  `test_detector_does_not_flag_an_arbitrarily_named_external_factory_call`; left unchanged.
- **Finding 2** (`[P2]` detect retry wrappers rather than only direct imports) is already closed:
  a decorator's literal resolved name (`retry`) matches `_RETRY_WRAPPER_CALL_NAMES` regardless of
  which module it was imported from. A synthetic reproduction of
  `from trading_research.retry_helpers import retry; @retry def retry_external_paper_order(): ...`
  returned `["decorator 'retry' on retry_external_paper_order at line 3"]`, not `[]`. Already
  fixed (pre-existing behavior); left unchanged.
- **Finding 3** (`[P1]` unguarded broker-submission helper) is already closed:
  `_submit_checkpointed_attempt` is a member of `_PROTECTED_FUNCTIONS` at reviewed HEAD (closed in
  round 3). A synthetic decorated `_submit_checkpointed_attempt` was flagged, not `[]`. Already
  fixed; left unchanged.
- **Finding 4** (`[P2]` post-definition retry wrappers) is already closed by the round-4
  reassignment scan. A synthetic
  `_submit_checkpointed_attempt = broker_retry(_submit_checkpointed_attempt)` module-level
  reassignment was flagged, not `[]`. Already fixed; left unchanged.
- **Finding 5** (`[P2]` assignment aliases bypassing the retry guard) is already closed by the
  round-7 `_resolve_import_aliases` name-to-name chain. A synthetic
  `broker_retry = retry; @broker_retry def _do_submit(): ...` called from
  `retry_external_paper_order` was flagged, not `[]`. Already fixed; left unchanged.
- **Finding 6** (`[P2]` match-case rebinding bypasses) is the same confirmed gap as the `match`
  portion of Finding 1 above -- fixed by the same `_rebind_offenders_in_block` /
  `_module_scope_statements` change.
- **Finding 7** (`[P2]` aliased helper calls escaping the retry guard) was a **confirmed bypass**:
  `_resolve_import_aliases` records helper name-to-name aliases, but `_direct_local_calls` (used
  by `_transitively_called_local_helpers` to build the call graph) matched only the callee's own
  bare name against `local_functions`, never resolving it through that alias map. A synthetic
  `@retry def _do_submit(): ...; submit = _do_submit; def retry_external_paper_order(): submit()`
  returned `[]`: because the alias broke the call-graph edge, `_do_submit` was never reachable
  from the protected entry points, so its own `@retry` decorator was never even inspected (the
  decorator/call checks only run on functions named directly in `_PROTECTED_FUNCTIONS` or
  discovered as reachable). Fixed by having `_direct_local_calls` resolve each call's bare name
  through the same `aliases` map before checking `local_functions` membership, and threading
  `aliases` through `_transitively_called_local_helpers`.

Fix commit `b0c501568e8404e7ec8bdd930018621a8110ab93` closes the three confirmed bypasses (match-
case rebinding, except* rebinding, aliased-helper-call reachability) and adds six regression
tests: three positive (one per confirmed bypass) and two negative controls guarding against
overreach (an unrelated match-case-nested alias must not misfire; aliasing and calling an
ordinary, undecorated helper must not fabricate an offender), plus the Python-3.11-gated except*
test. Reproduced Findings 2-6's already-fixed status with fresh synthetic modules against the
pre-fix detector rather than trusting the prior rounds' documentation alone.

Validation: confirmed each of the three bypasses against the pre-fix detector with ad hoc
synthetic-module scripts (not the pytest file) before changing any code. Post-fix,
`pytest tests/unit/test_external_broker_no_tenacity_import_boundary.py -q` passed 43/43,
including the six new regression tests. Full `nox -s ci` (`ci`, `tests` [3226 passed,
106 skipped], `paper_tests` [160 passed], `safety_typecheck` [0 errors], `migration_smoke`)
passed.
