# Code Review Findings

## Review Metadata

- Repository: `/Users/jijopaul/workspace/ai_stock_trading_v2`
- Branch: `migration/14-apscheduler-tenacity-feasibility`
- Reviewed HEAD: `ffdefb0c2e494ae3ef22f2b8c314e146b6e3b0c4`
- Subject: Record PR 14 fix round 2: transitive submission-helper guard closed
- Claude commits reviewed: 4f261aa81b77117c711bc77c4a4045f58863d444,76b399d1a270388425fb28884962b8a4c852ddf6
- Review scope: FULL_PR
- Reviewed base: `c32021ef75174bc2271971626eb928fff83d1069`
- GitHub PR: #32
- Fix round: 2
- Trigger: local Git `post-commit`
- Review status: FIXES_APPLIED_PENDING_REVIEW
- Highest priority: none
- Finding count: 0
- Fix commit: `82860bcb28f730d983f1100cc3639fb092883f68`

## Resolution

- Confirmed the shared root cause behind all three findings: `_find_protected_function_offenders`
  matched call names by literal spelling (`retry`/`Retrying`) only, so (1) an import-aliased name
  (e.g. `from retry_utils import Retrying as broker_retrying` then `broker_retrying(...)`) and
  (2) a retry-decorated or retry-calling helper function that a protected function delegates to by
  bare-name call, without itself being named in `_PROTECTED_FUNCTIONS`, both passed with zero
  offenders. Separately, `_find_tenacity_import_offenders` only recognized static
  `ast.Import`/`ast.ImportFrom` nodes, missing a dynamic
  `importlib.import_module("tenacity")`/`__import__("tenacity")` call (the third form named in the
  P2 GitHub thread). Reproduced all three with synthetic modules before changing any code.
- `_submit_checkpointed_attempt` (the P1 finding) was already added to `_PROTECTED_FUNCTIONS` in
  fix round 2 (`76b399d1a270388425fb28884962b8a4c852ddf6`) and remains covered; no further change
  was needed for that specific function.
- Fixed in `tests/unit/test_external_broker_no_tenacity_import_boundary.py`:
  - Added `_resolve_import_aliases`/`_resolved_call_name` to resolve import aliases before
    comparing a call's name against `{"retry", "Retrying"}`, closing the aliased-import bypass.
  - Added `_module_level_functions`/`_direct_local_calls`/`_transitively_called_local_helpers` to
    compute the local, bare-name call graph and apply a narrower check (retry-shaped
    decorators/calls only, not "any decorator") to every module-level helper transitively reached
    from the four protected functions. The check is deliberately narrower than the "any decorator"
    rule kept for the four named functions, because real code already has a legitimate, unrelated
    decorator that far out (`_order_lease`'s `@contextlib.contextmanager`, called directly by
    `retry_external_paper_order` and `refresh_retry_preview`) — verified this before implementing,
    to avoid a false positive against existing code.
  - Extended `_find_tenacity_import_offenders` to flag `importlib.import_module("tenacity")` and
    `__import__("tenacity")` calls alongside static imports.
- Documented one residual, deliberately accepted gap in both the module docstring and
  `_find_protected_function_offenders`'s docstring: a project-local or third-party wrapper
  imported under a name unrelated to `retry`/`Retrying`, and never itself calling something by
  those names (e.g. `from retry_utils import broker_retrying` where `broker_retrying` is an
  arbitrary-named factory defined in that external module), cannot be distinguished from an
  ordinary helper call by parsing this one file alone. Closing it would require inspecting an
  arbitrary external module's source (defeating this test's zero-dependency, always-runs design)
  or banning all bare imported-name calls in protected code (which would misfire on legitimate
  future helpers). This is the same category of limitation the module's top docstring already
  accepted for indirection generally. Recorded as `test_detector_does_not_flag_an_arbitrarily_named_external_factory_call`.
- Added regression coverage (6 new tests, 14 total in the file):
  `test_detector_flags_a_synthetic_dynamic_tenacity_import`,
  `test_detector_flags_an_aliased_retrying_call`,
  `test_detector_flags_a_retry_decorated_helper_transitively_called_by_a_protected_function`,
  `test_detector_flags_a_retry_call_inside_a_transitively_called_helper`,
  `test_detector_does_not_flag_unrelated_decorators_on_transitively_called_helpers` (guards against
  the `_order_lease` false-positive), and
  `test_detector_does_not_flag_an_arbitrarily_named_external_factory_call` (documents the accepted
  gap).
- Validation passed: 14/14 focused tests in
  `tests/unit/test_external_broker_no_tenacity_import_boundary.py`, and `nox -s ci` in full
  (`ci`: success; `tests`: 3197 passed/106 skipped; `paper_tests`: 160 passed;
  `safety_typecheck`: 0 errors/0 warnings; `migration_smoke`: OK).

## Findings

None outstanding.

## Findings as reviewed (historical record)

### [P2] Investigate: aliased retry callables bypass the broker retry boundary guard

**Commit:** `4f261aa81b77117c711bc77c4a4045f58863d444`

**Location:** [/Users/jijopaul/workspace/ai_stock_trading_v2/tests/unit/test_external_broker_no_tenacity_import_boundary.py:84](/Users/jijopaul/workspace/ai_stock_trading_v2/tests/unit/test_external_broker_no_tenacity_import_boundary.py:84)

**Concern:** The structural test may not prove the required absence of Tenacity-backed retry behavior around the ambiguous broker-submission path. It rejects decorators on four named functions, but inside those functions it recognizes retry wrappers only when the invoked name is literally `retry` or `Retrying`.

**Evidence:** `_find_protected_function_offenders` compares the final call name against `{"retry", "Retrying"}` at lines 102–107. A synthetic protected `_submit_checkpointed_attempt` using an indirectly imported `broker_retrying()` callable around `runtime.submit_limit_order(...)` produced:

```text
tenacity_import_offenders= []
protected_function_offenders= []
```

This is valid Tenacity usage when `broker_retrying` is an alias or project-local factory returning `tenacity.Retrying`. The later extension in `76b399d1a270388425fb28884962b8a4c852ddf6` protects the current shared submission helper, but does not close this name-based bypass. The master plan requires structural proof that no `@retry`/`Retrying()` usage wraps the ambiguous-broker-retry path.

**Potential impact if confirmed:** A future Tenacity adoption could automatically repeat `runtime.submit_limit_order(...)` after an ambiguous outcome, bypassing authoritative `NOT_FOUND` evidence, explicit operator retry, and retry-limit gates. That could create duplicate external paper orders.

**Investigation and conditional remediation:** First verify the concern against the current detector using aliased `Retrying`, a project-local retry factory, and a retry-decorated helper transitively called by the broker-call boundary. If confirmed, make the guard track Tenacity aliases and/or structurally protect the submission call path without relying solely on callable spellings, then add regression cases for each confirmed bypass. If the concern is disproved or another current guard already covers these forms, document that evidence and leave the code unchanged.

**Validation:** Add negative fixtures showing that direct, aliased, project-local, and transitively delegated retry wrappers around `runtime.submit_limit_order(...)` all fail the structural test, while ordinary transport-retry code outside the external-broker submission path remains permitted.

Tests or diagnostics run: reviewed both requested full diffs and relevant HEAD call paths; ran a read-only synthetic AST diagnostic demonstrating the bypass. The canonical targeted Nox session could not run because `nox` is not installed in the environment. No files were modified.

### [P2] Investigate: Detect retry wrappers rather than only direct imports

Commit: `4f261aa81b77117c711bc77c4a4045f58863d444`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/tests/unit/test_external_broker_no_tenacity_import_boundary.py:36`

Concern: An active GitHub review thread raises the following potentially valid issue:

> **<sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub>  Detect retry wrappers rather than only direct imports**
> 
> Investigate whether this guard satisfies `MASTER_PLAN.md` row 14’s requirement to prove that no `@retry`/`Retrying()` usage wraps the ambiguous broker-retry path: it only rejects direct `tenacity` imports, so a future Tenacity adoption can place `retry` or `Retrying` in a shared helper and import that helper here, or load Tenacity dynamically, while this test still passes and an ambiguous submission may be retried automatically. This is reproducible by parsing synthetic modules containing `from trading_research.retry_helpers import retry; @retry ...` or an imported `Retrying()` wrapper—the detector returns no offenders. If that confirms the gap, extend the structural boundary to detect retry wrapping of the relevant functions and add those indirect cases as regression tests; otherwise record how another enforced boundary excludes them.
> 
> AGENTS.md reference: [AGENTS.md:L60-L69](https://github.com/jijoece/ai_stock_trading_v2/blob/4f261aa81b77117c711bc77c4a4045f58863d444/AGENTS.md#L60-L69)
> 
> Useful? React with 👍 / 👎.

Evidence: [chatgpt-codex-connector review thread](https://github.com/jijoece/ai_stock_trading_v2/pull/32#discussion_r3889721104) is current, unresolved, and not outdated.

Potential impact if confirmed: Merging would carry the reported defect into main.

Investigation and conditional remediation: Verify the comment against the current code and reproduce the behavior where practical. If confirmed, fix it and add regression coverage. If it is invalid or already fixed, document the evidence and do not make an unnecessary code change.

Validation: Run the focused regression test and the repository's canonical validation; a subsequent full-PR review must find no remaining defect.

### [P1] Investigate: Investigate the unguarded broker-submission helper

Commit: `2e561ffbf1e27c75cf92f4de6f758d5e47f3a60f`

Location: `/Users/jijopaul/workspace/ai_stock_trading_v2/tests/unit/test_external_broker_no_tenacity_import_boundary.py:51`

Concern: An active GitHub review thread raises the following potentially valid issue:

> **<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)</sub></sub>  Investigate the unguarded broker-submission helper**
> 
> Fresh evidence after the earlier indirect-wrapper comment is that the new guard still omits `_submit_checkpointed_attempt`, even though `retry_external_paper_order` calls it and its `runtime.submit_limit_order(...)` at `external_broker.py:1296` is the actual ambiguous broker side effect. If a future shared `@broker_retry` decorator is applied to that helper, both detectors return no offenders while Tenacity may resubmit an order without fresh authoritative `NOT_FOUND` evidence. Verify with a synthetic decorated `_submit_checkpointed_attempt` (the current detector returns `[]`); if confirmed, protect the broker-call sink and add that regression case, otherwise record which enforced boundary excludes such wrapping.
> 
> AGENTS.md reference: [AGENTS.md:L60-L62](https://github.com/jijoece/ai_stock_trading_v2/blob/2e561ffbf1e27c75cf92f4de6f758d5e47f3a60f/AGENTS.md#L60-L62)
> 
> Useful? React with 👍 / 👎.

Evidence: [chatgpt-codex-connector review thread](https://github.com/jijoece/ai_stock_trading_v2/pull/32#discussion_r3889738750) is current, unresolved, and not outdated.

Potential impact if confirmed: Merging would carry the reported defect into main.

Investigation and conditional remediation: Verify the comment against the current code and reproduce the behavior where practical. If confirmed, fix it and add regression coverage. If it is invalid or already fixed, document the evidence and do not make an unnecessary code change.

Validation: Run the focused regression test and the repository's canonical validation; a subsequent full-PR review must find no remaining defect.

