"""Structural AST guard for library-migration PR 14 (`MASTER_PLAN.md` row 14,
`DECISIONS.md` D12): Tenacity's own scoping is decorator/context-manager
based, not a global interceptor, so it can be kept away from the ambiguous
external-broker-retry path (`paper_books/external_broker.py`) simply by never
importing it there. This test enforces that boundary structurally, the same
way `test_lumibot_import_boundary.py` enforces the LumiBot boundary — by
parsing source with `ast`, not by importing `tenacity` (which is not a
dependency of this repository), so it runs unconditionally in every
environment, never skips, and fails rather than silently passing if
`tenacity` is ever added as a dependency and someone wraps this exact module
with it.

The literal `import tenacity` / `from tenacity import ...` check alone only
catches a *direct* import in this file. It would pass if `external_broker.py`
instead imported a project-local decorator or callable that is itself backed
by Tenacity elsewhere (e.g. `from retry_utils import broker_retry` then
`@broker_retry` on `retry_external_paper_order`), or used a project-local
`Retrying(...)` context manager the same way. Because that indirection can
originate from any module name, it cannot be closed by tracing imports alone
without inspecting every transitively-imported module. Instead, the three
functions the master plan's row 14 names as the ambiguous-broker-retry path
-- `retry_external_paper_order`, `_prepare_external_retry_attempt`, and
`refresh_retry_preview` -- plus `_submit_checkpointed_attempt`, the shared
helper both `retry_external_paper_order` and the ordinary first-attempt path
(`_submit_once`) delegate to for the actual `runtime.submit_limit_order(...)`
call, are structurally forbidden from carrying *any* decorator and from
containing *any* call named `retry` or `Retrying` (the two exact Tenacity API
forms row 14 calls out: `@retry` and `Retrying()`), regardless of where that
name was imported from. Since none of the four currently use either form,
this is a zero-cost regression guard: any future change that wraps one of
them -- directly or indirectly -- fails this test and must justify updating
it explicitly.

Scope is deliberately this one file, not the whole `src/trading_research`
tree: `COMPONENT_MATRIX.md`'s "Generic transient retries" row leaves generic
per-provider transport retry code (`evidence_providers/http_client.py`)
eligible for a future, separately-approved Tenacity adoption; only the
ambiguous-broker-retry path is structurally excluded.

PR 14 review round 3 closed three further bypasses of the checks above: an
*aliased* import of `retry`/`Retrying` used as a call (e.g. `from tenacity
import Retrying as broker_retrying`); a retry-decorated helper function
delegated to (directly or transitively, by bare-name call) from one of the
protected functions without itself being named in `_PROTECTED_FUNCTIONS`;
and a dynamic `importlib.import_module("tenacity")`/`__import__("tenacity")`
call, which the static `ast.Import`/`ast.ImportFrom` check cannot see.

PR 14 review round 4 closed two more: a *module-level reassignment* of a
protected or transitively-called name to the result of a call (e.g.
`_submit_checkpointed_attempt = broker_retry(_submit_checkpointed_attempt)`
immediately after the `def`, which touches neither the function's own
`decorator_list` nor its body); and an *aliased or keyword-argument* form of
the dynamic-import check (`from importlib import import_module as load`
then `load("tenacity")`, or `import_module(name="tenacity")`), which the
literal-spelling, positional-args-only check from round 3 missed.

PR 14 review round 5 closed three more forms the round-4 reassignment scan
missed because it only matched a bare `ast.Assign` statement that is a
direct child of the module body: an *annotated* reassignment
(`_submit_checkpointed_attempt: object = broker_retry(...)`, an `ast.
AnnAssign` node, not `ast.Assign`); the same reassignment nested one level
inside a top-level `if`/`try`/`with` block (still module scope in Python,
but not a direct child of `tree.body`); and a bare *walrus* expression
statement (`(_submit_checkpointed_attempt := broker_retry(...))`, an `ast.
NamedExpr` wrapped in `ast.Expr`, not `ast.Assign` at all). It also closed a
`functools.partial(retry, ...)`-wrapped decorator on a protected function or
transitively-called helper, whose own call-name resolves to `partial`, not
`retry`/`Retrying`, one level short of where the round-3/4 checks look.

PR 14 review round 6 closed two more: the round-5 nested-block recursion
handled only `if`/`try`/`with`, missing the identical reassignment nested
one level inside a top-level `for`/`async for`/`while` block (also not a new
scope in Python); and a reassignment whose call is *composed* through
`functools.partial(retry, ...)` (`_do_submit = functools.partial(retry,
stop=3)(_do_submit)`) on a transitively-called helper, where the callee
(`value.func`) is itself the `functools.partial(...)` call -- one level
short of where the round-5 partial-unwrap, written only for decorators,
looked. Both the decorator check and the reassignment-callee check now share
one `_resolved_wrapper_name` helper for this unwrap.

PR 14 review round 7 closed one more: a plain module-scope name-to-name
reassignment of an already-resolved retry-shaped name (e.g. `broker_retry =
retry` after `from retry_utils import retry`, then `@broker_retry` on a
protected or transitively-called helper) was invisible to the alias
resolver, which previously tracked only `import ... as ...` aliases -- not
same-file, non-import rebinds of a name to a new local name. `_resolved_
import_aliases` now also chains simple `Name = Name` assignments anywhere at
true module scope (via the new `_module_scope_statements`, matching the
scope boundary the reassignment scan already enforces).

PR 14 review round 9 closed two more: (1) `_module_level_functions` only
examined direct children of `tree.body`, so a retry-decorated helper defined
one level inside a top-level `if`/`try`/`with`/`for`/`while`/`match`/
`except*` block (still module scope in Python) was invisible to the
call-graph reachability analysis -- a protected function delegating to it
went unchecked even though the helper's own decorator was never inspected.
It now walks the same `_module_scope_statements` traversal already used for
aliases and reassignments. (2) `_direct_local_calls` resolved a call's bare
name through the module-scope `aliases` chain (round 8) but not through a
same-named alias assigned inside the *calling function's own body* (e.g.
`def retry_external_paper_order(): submit = _do_submit; submit()`), which
broke the call-graph edge the same way round 8's module-scope gap did. The
new `_local_aliases_in_block` layers the caller's own simple `Name = Name`
rebinds on top of `aliases` before that resolution.

PR 14 review round 10 closed two more: (1) both the round-7 module-scope
alias chain and the round-9 function-local alias chain folded every visited
assignment into one unconditional `dict[str, str]`, so a name bound to a
retry-shaped value on only one of two mutually exclusive branches (e.g. `if
enabled: wrapper = retry / else: wrapper = ordinary`) could be silently
overwritten by that same name's binding on the other branch, hiding a
retry-wrapped path that remains executable whenever the branch holding the
retry binding is taken. Both alias chains are now built by
`_accumulate_name_bindings`, a small dataflow pass that threads a `name ->
{feasible resolved names}` state through a block, replacing the bound value
on a straight-line reassignment (a real overwrite) but *joining* -- via
`_merge_binding_states` -- the alternative endings of each branch of an
`if`/`try`/`match`/`for`/`while` (none of which are a real overwrite of each
other, since only one alternative executes at runtime and any is feasible).
`_direct_local_calls` and the retry-wrapper-name checks now resolve against
every feasible name in that set, not just one. (2) `_local_aliases_in_block`
also previously recognized only a bare, unannotated `ast.Assign`, so a
type-annotated local alias (`submit: object = _do_submit`) was invisible to
it the same way the round-7 module-scope resolver missed `ast.AnnAssign`
before this fix (`_accumulate_name_bindings` now handles both scopes'
`ast.AnnAssign` uniformly).

See `_find_protected_function_offenders` for the residual gaps this file
cannot structurally close by parsing `external_broker.py` alone: (1) a
project-local or third-party wrapper imported under a name unrelated to
`retry`/`Retrying` and never itself calling something by those names; (2) a
decorator or call on a *class method* (e.g. `OrderLeaseHandle.fenced_write`)
or on a closure nested inside a protected function's own body, neither of
which the call-graph reachability analysis below considers, since it only
tracks bare-name calls between module-level function definitions; and (3),
most fundamentally, anything done to the four protected names from *outside*
this file at runtime (monkeypatching, `globals()`/`setattr` rebinding, or a
generically-applied instrumentation wrapper) -- this file only ever parses
`external_broker.py`'s own static source text and has no visibility into any
other module or into anything done after import. Closing (3) would require a
runtime invariant in the call path itself, not more source-text pattern
matching; it is not attempted here.
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

_EXTERNAL_BROKER_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src" / "trading_research" / "paper_books" / "external_broker.py"
)

_PROTECTED_FUNCTIONS = frozenset({
    "retry_external_paper_order",
    "_prepare_external_retry_attempt",
    "refresh_retry_preview",
    # Shared broker-call boundary: both the retry path
    # (retry_external_paper_order) and the ordinary first-attempt path
    # (_submit_once) delegate here for the actual
    # runtime.submit_limit_order(...) call, so a wrapper placed here alone
    # -- bypassing the three functions above -- would still enable
    # unauthorized automatic retries of an ambiguous submission.
    "_submit_checkpointed_attempt",
})

_RETRY_WRAPPER_CALL_NAMES = frozenset({"retry", "Retrying"})

# `except*` (PEP 654) only parses starting with Python 3.11; this project's
# floor is 3.10 (`python-3-10-floor` CI job), so `ast.TryStar` may not exist
# on the interpreter running this file. Falls back to an empty tuple there,
# leaving `ast.Try` handling (which already covers plain `except`) untouched.
_TRY_NODE_TYPES: tuple[type, ...] = (
    (ast.Try, ast.TryStar) if hasattr(ast, "TryStar") else (ast.Try,)
)


def _find_tenacity_import_offenders(tree: ast.Module) -> list[str]:
    """PR 14 review round 4 closed two further dynamic-import bypasses: an
    *aliased* local name for `import_module`/`__import__` (e.g. `from
    importlib import import_module as load` then `load("tenacity")`, which
    a literal-spelling call-name comparison alone missed), and the `name=`
    keyword-argument form both functions accept
    (`import_module(name="tenacity")`, which a positional-args-only scan
    missed)."""
    aliases = _resolve_import_aliases(tree)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "tenacity" for a in node.names):
            offenders.append(f"import tenacity at line {node.lineno}")
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "tenacity":
            offenders.append(f"from tenacity import ... at line {node.lineno}")
        if isinstance(node, ast.Call) and _resolved_call_names(node.func, aliases) & {"import_module", "__import__"}:
            candidates = list(node.args) + [kw.value for kw in node.keywords if kw.arg == "name"]
            if any(
                isinstance(c, ast.Constant) and isinstance(c.value, str) and c.value.split(".")[0] == "tenacity"
                for c in candidates
            ):
                offenders.append(f"dynamic import of tenacity at line {node.lineno}")
    return offenders


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _module_scope_statements(statements: list[ast.stmt]) -> list[ast.stmt]:
    """Every statement at true module scope, recursing into
    `if`/`try`/`with`/`async with`/`for`/`async for`/`while` blocks (none of
    which introduce a new scope in Python) without ever descending into a
    `def`/`class` body (which do). Used by `_resolve_import_aliases` (PR 14
    review round 7) to find simple name-to-name reassignments anywhere at
    real module scope, matching the same scope boundary
    `_rebind_offenders_in_block` already enforces for protected-function
    reassignment.

    PR 14 review round 8 added `match` (whose `case` bodies do not introduce
    a new scope either) and `except*` (`ast.TryStar`, alongside plain `try`)
    to the recursion, matching the same two additions to
    `_rebind_offenders_in_block` below."""
    collected: list[ast.stmt] = []
    for statement in statements:
        collected.append(statement)
        if isinstance(statement, ast.If):
            collected.extend(_module_scope_statements(statement.body))
            collected.extend(_module_scope_statements(statement.orelse))
        elif isinstance(statement, _TRY_NODE_TYPES):
            collected.extend(_module_scope_statements(statement.body))
            for handler in statement.handlers:
                collected.extend(_module_scope_statements(handler.body))
            collected.extend(_module_scope_statements(statement.orelse))
            collected.extend(_module_scope_statements(statement.finalbody))
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            collected.extend(_module_scope_statements(statement.body))
        elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            collected.extend(_module_scope_statements(statement.body))
            collected.extend(_module_scope_statements(statement.orelse))
        elif isinstance(statement, ast.Match):
            for case in statement.cases:
                collected.extend(_module_scope_statements(case.body))
    return collected


def _merge_binding_states(
    states: list[dict[str, frozenset[str]]],
) -> dict[str, frozenset[str]]:
    """Joins several alternative ending states of the same name-binding
    dataflow (e.g. the `if` body and `orelse` paths, or every `match` `case`)
    into one state where each name maps to the union of every value it could
    hold coming out of any one alternative -- since only one alternative
    actually executes at runtime, but any of them is feasible."""
    merged: dict[str, frozenset[str]] = {}
    for key in {key for state in states for key in state}:
        merged[key] = frozenset().union(*(state.get(key, frozenset()) for state in states))
    return merged


def _accumulate_name_bindings(
    statements: list[ast.stmt], state: dict[str, frozenset[str]]
) -> dict[str, frozenset[str]]:
    """Threads a name -> {possible resolved names} binding state through a
    block of module- (or function-) scope statements, used to build the
    assignment-alias chain both `_resolve_import_aliases` and
    `_local_aliases_in_block` need.

    PR 14 review round 9's investigation (round 10 fix) found that folding
    every visited statement into one unconditional `dict[str, str]` -- the
    approach every prior round used -- silently collapsed mutually exclusive
    control-flow paths: `if enabled: wrapper = retry / else: wrapper =
    ordinary` left `wrapper` resolved only to `ordinary`, whichever branch's
    assignment happened to be visited last, even though the `retry` binding
    is a feasible runtime value whenever `enabled` is true. A straight-line
    reassignment (`x = retry` followed later, same block, by `x =
    ordinary`) is a real overwrite and should still discard the earlier
    value; only assignments on mutually exclusive branches are alternatives
    that must both survive. This function tells the two apart: within one
    straight-line list of statements, a later `Assign`/`AnnAssign` to the
    same name replaces the state precisely as Python does; at every branch
    construct (`if`/`else`, each `try`/`except`/`else`, each `match` `case`,
    the body of `for`/`while` versus never entering it), the state per
    alternative is computed independently from the same incoming state and
    then joined via `_merge_binding_states`, so a name flows out bound to
    every value any one reachable path could have given it. Never descends
    into a nested `def`/`class` body, which do introduce a new scope."""
    for statement in statements:
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and isinstance(statement.value, ast.Name)
        ):
            target_name = statement.targets[0].id
            source_name = statement.value.id
            state = {**state, target_name: state.get(source_name, frozenset({source_name}))}
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and isinstance(statement.value, ast.Name)
        ):
            target_name = statement.target.id
            source_name = statement.value.id
            state = {**state, target_name: state.get(source_name, frozenset({source_name}))}
        elif isinstance(statement, ast.If):
            state = _merge_binding_states([
                _accumulate_name_bindings(statement.body, state),
                _accumulate_name_bindings(statement.orelse, state),
            ])
        elif isinstance(statement, _TRY_NODE_TYPES):
            branch_states = [_accumulate_name_bindings(statement.body, state)]
            for handler in statement.handlers:
                branch_states.append(_accumulate_name_bindings(handler.body, state))
            branch_states.append(_accumulate_name_bindings(statement.orelse, state))
            state = _accumulate_name_bindings(
                statement.finalbody, _merge_binding_states(branch_states)
            )
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            state = _accumulate_name_bindings(statement.body, state)
        elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            state = _merge_binding_states([
                state,
                _accumulate_name_bindings(statement.body, state),
                _accumulate_name_bindings(statement.orelse, state),
            ])
        elif isinstance(statement, ast.Match):
            branch_states = [state] + [
                _accumulate_name_bindings(case.body, state) for case in statement.cases
            ]
            state = _merge_binding_states(branch_states)
    return state


def _resolve_import_aliases(tree: ast.Module) -> dict[str, frozenset[str]]:
    """Maps each locally-used name to every name it could ultimately resolve
    to, so `broker_retrying` from `from tenacity import Retrying as
    broker_retrying` still resolves to `Retrying` for the retry-wrapper-call
    check -- closing the aliased-import gap a literal-spelling comparison
    alone missed (PR 14 review round 3).

    PR 14 review round 7 closed a further gap: a plain module-scope
    name-to-name reassignment (e.g. `broker_retry = retry` after `from
    retry_utils import retry`, then `@broker_retry` on a protected or
    transitively-called helper) was invisible to this resolver, which
    previously tracked only `import ... as ...` aliases -- so a same-file,
    non-import rebind of a retry-shaped name to a new local name bypassed
    both the decorator and inner-call checks.

    PR 14 review round 10 closed a third: the round-7 chain (and its round-9
    function-local counterpart, `_local_aliases_in_block`) folded every
    branch of a module-scope `if`/`try`/`match`/etc. into one unconditional
    `dict[str, str]`, so a name bound to a retry-shaped value on only one of
    two mutually exclusive branches could be silently overwritten by that
    same name's binding on the other branch. Every locally-used name now
    resolves to a `frozenset` of every value it could feasibly hold, built by
    `_accumulate_name_bindings`, which joins branch alternatives instead of
    letting one overwrite another."""
    aliases: dict[str, frozenset[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = frozenset({alias.name})
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = frozenset({alias.name.split(".")[-1]})
    return _accumulate_name_bindings(tree.body, aliases)


def _resolved_call_names(node: ast.expr, aliases: dict[str, frozenset[str]]) -> frozenset[str]:
    name = _call_name(node)
    if name is None:
        return frozenset()
    return aliases.get(name, frozenset({name}))


def _resolved_wrapper_names(node: ast.expr, aliases: dict[str, frozenset[str]]) -> frozenset[str]:
    """Resolves every ultimate retry-wrapper name a decorator, or the callee
    of a module-level reassignment's call, could resolve to -- unwrapping one
    level of `functools.partial(retry, ...)` composition (PR 14 review round
    5) so the retry-shaped callable it wraps is not hidden behind `partial`'s
    own name. Shared by the decorator check (`@functools.partial(retry,
    stop=3)`) and the reassignment-callee check (PR 14 review round 6:
    `_do_submit = functools.partial(retry, stop=3)(_do_submit)`, where the
    callee -- `value.func` -- is itself the `functools.partial(...)` call).
    Returns a `frozenset` (PR 14 review round 10) since `aliases` may now map
    a single name to more than one feasible branch-dependent binding."""
    target = node.func if isinstance(node, ast.Call) else node
    names = _resolved_call_names(target, aliases) or frozenset({ast.dump(target)})
    if "partial" in names and isinstance(node, ast.Call):
        wrapped = list(node.args[:1]) + [kw.value for kw in node.keywords if kw.arg == "func"]
        wrapped_names: set[str] = set()
        for candidate in wrapped:
            wrapped_names |= _resolved_call_names(candidate, aliases)
        retry_shaped = wrapped_names & _RETRY_WRAPPER_CALL_NAMES
        if retry_shaped:
            names = (names - {"partial"}) | retry_shaped
    return names


def _module_level_functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function defined at true module scope, recursing into the same
    non-scope-introducing `if`/`try`/`with`/`for`/`while`/`match`/`except*`
    blocks `_module_scope_statements` already recurses into for aliases and
    reassignments (PR 14 review round 9): a `def` nested one level inside a
    top-level `if:` block is still a module-level function in Python -- `if`
    does not introduce a new scope -- but a bare scan of `tree.body`'s direct
    children missed it, making such a helper (and any retry decorator it
    carries) invisible to the call-graph reachability analysis below,
    regardless of whether a protected function calls it."""
    return {
        node.name: node
        for node in _module_scope_statements(tree.body)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _local_aliases_in_block(
    statements: list[ast.stmt], aliases: dict[str, frozenset[str]]
) -> dict[str, frozenset[str]]:
    """Same `Name`/annotated-`Name` = `Name` chaining `_resolve_import_aliases`
    performs at module scope, but applied to one function's own statements
    (via `_accumulate_name_bindings`, so it still never descends into a
    nested `def`/`class` body). Closes a function-local counterpart of the
    round-7 module-scope assignment-aliasing gap (PR 14 review round 9):
    `submit = _do_submit` followed by `submit()`, both written inside the
    calling function's own body rather than at module scope, previously left
    `submit` unresolved by `_direct_local_calls`.

    PR 14 review round 10 closed two more forms of the same gap, sharing the
    fix with `_resolve_import_aliases`: an *annotated* local alias (`submit:
    object = _do_submit`, an `ast.AnnAssign` the round-9 version ignored
    entirely), and a local alias bound on only one of two mutually exclusive
    branches (`if enabled: submit = _do_submit / else: submit = ordinary`),
    which the round-9 version's single-value dict collapsed to whichever
    branch was visited last, discarding a still-feasible retry-decorated
    binding."""
    return _accumulate_name_bindings(statements, aliases)


def _direct_local_calls(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    local_functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    aliases: dict[str, frozenset[str]],
) -> set[str]:
    """Bare-name calls only (`helper(...)`), never attribute calls
    (`repo.helper(...)`), so an unrelated method that happens to share a
    local function's name can never fabricate a call-graph edge.

    PR 14 review round 8: the call's bare name is resolved through
    `aliases` (the same module-scope name-to-name chain
    `_resolve_import_aliases` already builds) before the `local_functions`
    membership check, so `submit = _do_submit` followed by `submit()`
    still creates a call-graph edge to `_do_submit`. Without this, a
    protected function delegating only through such an alias made
    `_do_submit` unreachable from `entry_points`, so its own `@retry`
    decorator was never even inspected -- the decorator/call checks in
    `_find_protected_function_offenders` only run on functions named
    directly in `_PROTECTED_FUNCTIONS` or discovered as reachable here.

    PR 14 review round 9: the round-8 fix only resolved *module-scope*
    aliases. A same-named alias assigned inside the calling function's own
    body (e.g. `def retry_external_paper_order(): submit = _do_submit;
    submit()`) was invisible to it, breaking the call-graph edge the same
    way. `_local_aliases_in_block` layers the caller's own simple
    `Name = Name` rebinds on top of the module-scope `aliases` before
    resolving each call's bare name.

    PR 14 review round 10: `aliases`/`local_aliases` now map a name to every
    value it could feasibly hold across mutually exclusive branches, so a
    call through a branch-dependent alias adds a call-graph edge to *every*
    feasible callee, not just whichever branch's binding happened to be
    resolved -- a protected function must not be able to reach a
    retry-decorated helper only on a code path this analysis failed to
    consider."""
    local_aliases = _local_aliases_in_block(node.body, aliases)
    called = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
            resolved = local_aliases.get(inner.func.id, frozenset({inner.func.id}))
            called.update(resolved & local_functions.keys())
    return called


def _transitively_called_local_helpers(
    tree: ast.Module, entry_points: frozenset[str], aliases: dict[str, frozenset[str]],
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """The module-level functions in this file reachable (directly or
    indirectly, by bare-name call, resolving simple aliases of that name)
    from `entry_points`, excluding the entry points themselves. Closes the
    "retry-decorated helper transitively called by the broker-call
    boundary" gap: a wrapper need not sit on one of the four named
    functions directly if it can instead sit on a helper one of them
    delegates to (PR 14 review round 3)."""
    local_functions = _module_level_functions(tree)
    reachable: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    seen = set(entry_points)
    frontier = set(entry_points)
    while frontier:
        name = frontier.pop()
        node = local_functions.get(name)
        if node is None:
            continue
        for callee in _direct_local_calls(node, local_functions, aliases):
            if callee in seen:
                continue
            seen.add(callee)
            reachable[callee] = local_functions[callee]
            frontier.add(callee)
    return reachable


def _find_protected_function_offenders(tree: ast.Module) -> list[str]:
    """Flags any decorator, or any call named `retry`/`Retrying` (after
    resolving import aliases), attached to or used inside one of
    `_PROTECTED_FUNCTIONS` -- regardless of what module that name was
    imported from -- and also flags retry-shaped decorators/calls on any
    module-level helper transitively called (by bare-name call) from one of
    those functions. This closes the indirect-wrapper gap that
    `_find_tenacity_import_offenders` alone cannot: a project-local name that
    is itself backed by Tenacity elsewhere never triggers a literal `tenacity`
    import node in this file. It also closes two gaps found in PR 14 review
    round 3: (1) an *aliased* import of `retry`/`Retrying` (e.g.
    `from tenacity import Retrying as broker_retrying`) previously bypassed
    the literal-spelling call-name check; (2) a retry-decorated helper never
    named in `_PROTECTED_FUNCTIONS` but delegated to by one of them (e.g.
    `retry_external_paper_order` calling a local `_do_submit()` that itself
    carries `@retry`) previously went unchecked because the AST walk over a
    protected function's own body does not descend into a sibling
    function's definition.

    PR 14 review round 4 closed a third gap: a *module-level reassignment*
    of a protected or transitively-called name to the result of a call --
    e.g. `_submit_checkpointed_attempt = broker_retry(_submit_checkpointed_
    attempt)` immediately after its `def` -- never touches the function's
    own `decorator_list` or body, so neither the decorator check nor the
    inner-call check above would ever see it. Only top-level (module-body)
    assignments are inspected, matching how a decorator can only rebind the
    name it decorates at module scope; a same-named local variable inside
    an unrelated function body is not a redefinition of the protected name
    and is deliberately not flagged.

    PR 14 review round 6 closed two more: (1) a module-level reassignment
    nested inside a top-level `for`/`async for`/`while` block -- like
    `if`/`try`/`with` before it (round 5), these do not introduce a new
    scope, so `_rebind_offenders_in_block` now recurses into them too; (2) a
    reassignment whose call is *composed* through `functools.partial(retry,
    ...)`, e.g. `_do_submit = functools.partial(retry, stop=3)(_do_submit)`
    -- the callee (`value.func`) is itself the `functools.partial(...)` call,
    one level short of where the round-5 decorator-only partial-unwrap
    looked. Both the decorator check and the reassignment-callee check now
    share `_resolved_wrapper_name` for this unwrap.

    Helpers reached only transitively are checked narrowly -- retry-shaped
    decorators/calls/reassignments only, not "any decorator" -- because
    legitimate, unrelated decorators already exist on functions this path
    reaches (e.g. `_order_lease`'s `@contextlib.contextmanager`, called
    directly by `retry_external_paper_order` and `refresh_retry_preview`);
    banning any decorator that far out would misfire on real code. The four
    functions named in `_PROTECTED_FUNCTIONS` keep the stricter "any
    decorator"/"any reassignment" rule since the master plan names them
    directly and none legitimately carries either form today.

    PR 14 review round 8 closed three more: (1) and (2) a module-level
    reassignment of a protected or transitively-called name nested inside a
    `match` statement's `case` body, or inside an `except*` (`ast.TryStar`)
    handler -- both are non-scope-introducing blocks like the `if`/`try`/
    `with`/`for`/`while` forms already handled by rounds 5-6, but neither
    `_rebind_offenders_in_block` nor `_resolve_import_aliases`'s
    `_module_scope_statements` helper recursed into them; (3) a protected
    function delegating to a retry-decorated helper only through a local
    alias of that helper's name (e.g. `submit = _do_submit` then
    `submit()`) previously made the helper unreachable from
    `_transitively_called_local_helpers`, so its own decorator was never
    even inspected -- `_direct_local_calls` now resolves the call's bare
    name through the same alias chain before checking call-graph
    membership.

    Known, accepted residual gap: a project-local or third-party wrapper
    imported under a name that is neither an alias of `retry`/`Retrying` nor
    itself calls something named `retry`/`Retrying` (e.g. `from retry_utils
    import broker_retrying` where `broker_retrying` is a same-file-invisible
    factory defined elsewhere) cannot be distinguished, by parsing this file
    alone, from an ordinary helper call -- the same limitation this module's
    top docstring already documents for indirection generally. Closing it
    would require inspecting arbitrary external modules (defeating the
    zero-dependency, always-runs design of this test) or banning all
    bare imported-name calls in protected code, which would misfire on
    legitimate future helpers. See
    `test_detector_does_not_flag_an_arbitrarily_named_external_factory_call`
    for the documented, deliberately-accepted current behavior.
    """
    aliases = _resolve_import_aliases(tree)
    helpers = _transitively_called_local_helpers(tree, _PROTECTED_FUNCTIONS, aliases)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_named_protected = node.name in _PROTECTED_FUNCTIONS
        if not is_named_protected and node.name not in helpers:
            continue
        for decorator in node.decorator_list:
            names = _resolved_wrapper_names(decorator, aliases)
            matched = names & _RETRY_WRAPPER_CALL_NAMES
            if is_named_protected or matched:
                name = sorted(matched)[0] if matched else sorted(names)[0]
                offenders.append(f"decorator {name!r} on {node.name} at line {decorator.lineno}")
        for statement in node.body:
            for inner in ast.walk(statement):
                if isinstance(inner, ast.Call):
                    matched = _resolved_call_names(inner.func, aliases) & _RETRY_WRAPPER_CALL_NAMES
                    if matched:
                        offenders.append(
                            f"call to {sorted(matched)[0]!r} inside {node.name} at line {inner.lineno}"
                        )
    offenders.extend(_rebind_offenders_in_block(tree.body, helpers, aliases))
    return offenders


def _rebind_offenders_in_block(
    statements: list[ast.stmt],
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    aliases: dict[str, frozenset[str]],
) -> list[str]:
    """PR 14 review round 5: the round-4 reassignment scan only matched a
    bare `ast.Assign` that was a direct child of `tree.body`, missing three
    forms that still rebind the name at true module scope in Python: an
    annotated assignment (`ast.AnnAssign`); the identical assignment nested
    one level inside a top-level `if`/`try`/`with` block (those blocks do
    not introduce a new scope, unlike `def`/`class`, which this function
    deliberately never recurses into -- a same-named local rebind inside an
    unrelated function or class body is not a redefinition of the module-
    level name); and a bare walrus expression statement (`ast.NamedExpr`
    wrapped in `ast.Expr`, e.g. `(_do_submit := retry(_do_submit))`).

    PR 14 review round 6 closed a fourth: the same nested-block gap for
    `for`/`async for`/`while` blocks, which do not introduce a new scope
    either but were omitted from the round-5 recursion (which only handled
    `if`/`try`/`with`).

    PR 14 review round 8 closed a fifth and sixth: a `match` statement's
    `case` bodies do not introduce a new scope either (like `if`/`try`/
    `with`/`for`/`while` before them) but were never recursed into, and
    `except*` (`ast.TryStar`, PEP 654) was omitted from the round-4 `try`
    handling entirely -- both previously let a reassignment of a protected
    or transitively-called name to a retry-wrapper call pass unnoticed.
    """
    offenders: list[str] = []
    for statement in statements:
        target: ast.Name | None = None
        value: ast.expr | None = None
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
            for candidate in statement.targets:
                if isinstance(candidate, ast.Name):
                    target, value = candidate, statement.value
                    offenders.extend(_rebind_offender(target, value, helpers, aliases, statement.lineno))
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and isinstance(statement.value, ast.Call)
        ):
            offenders.extend(
                _rebind_offender(statement.target, statement.value, helpers, aliases, statement.lineno)
            )
        elif (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.NamedExpr)
            and isinstance(statement.value.target, ast.Name)
            and isinstance(statement.value.value, ast.Call)
        ):
            offenders.extend(
                _rebind_offender(
                    statement.value.target, statement.value.value, helpers, aliases, statement.lineno
                )
            )

        if isinstance(statement, ast.If):
            offenders.extend(_rebind_offenders_in_block(statement.body, helpers, aliases))
            offenders.extend(_rebind_offenders_in_block(statement.orelse, helpers, aliases))
        elif isinstance(statement, _TRY_NODE_TYPES):
            offenders.extend(_rebind_offenders_in_block(statement.body, helpers, aliases))
            for handler in statement.handlers:
                offenders.extend(_rebind_offenders_in_block(handler.body, helpers, aliases))
            offenders.extend(_rebind_offenders_in_block(statement.orelse, helpers, aliases))
            offenders.extend(_rebind_offenders_in_block(statement.finalbody, helpers, aliases))
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            offenders.extend(_rebind_offenders_in_block(statement.body, helpers, aliases))
        elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
            offenders.extend(_rebind_offenders_in_block(statement.body, helpers, aliases))
            offenders.extend(_rebind_offenders_in_block(statement.orelse, helpers, aliases))
        elif isinstance(statement, ast.Match):
            for case in statement.cases:
                offenders.extend(_rebind_offenders_in_block(case.body, helpers, aliases))
    return offenders


def _rebind_offender(
    target: ast.Name,
    value: ast.Call,
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    aliases: dict[str, frozenset[str]],
    lineno: int,
) -> list[str]:
    is_named_protected = target.id in _PROTECTED_FUNCTIONS
    if not is_named_protected and target.id not in helpers:
        return []
    names = _resolved_wrapper_names(value.func, aliases)
    matched = names & _RETRY_WRAPPER_CALL_NAMES
    if is_named_protected or matched:
        name = sorted(matched)[0] if matched else sorted(names)[0]
        return [f"module-level reassignment of {target.id!r} to {name!r} at line {lineno}"]
    return []


def test_no_tenacity_import_in_external_broker():
    tree = ast.parse(_EXTERNAL_BROKER_PATH.read_text())
    assert _find_tenacity_import_offenders(tree) == []


def test_no_decorator_or_retry_wrapper_call_in_protected_functions():
    tree = ast.parse(_EXTERNAL_BROKER_PATH.read_text())
    assert _find_protected_function_offenders(tree) == []


def test_detector_flags_a_synthetic_tenacity_import(tmp_path):
    """Proves the detector actually fires, rather than vacuously passing --
    mirrors `test_lumibot_import_boundary.py`'s corresponding proof test."""
    offending_module = tmp_path / "synthetic_external_broker.py"
    offending_module.write_text(
        "from tenacity import retry\n\n"
        "@retry\n"
        "def retry_external_paper_order():\n"
        "    pass\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == [
        "from tenacity import ... at line 1",
    ]


def test_detector_flags_a_synthetic_plain_import_tenacity(tmp_path):
    offending_module = tmp_path / "synthetic_external_broker_plain_import.py"
    offending_module.write_text("import tenacity\n")

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == ["import tenacity at line 1"]


def test_detector_flags_an_indirect_project_local_decorator(tmp_path):
    """The scenario the import-only check misses: a project-local decorator
    that never literally imports `tenacity` in this file, applied to a
    protected function. `_find_tenacity_import_offenders` passes vacuously;
    `_find_protected_function_offenders` must catch it."""
    offending_module = tmp_path / "synthetic_external_broker_indirect_decorator.py"
    offending_module.write_text(
        "from retry_utils import broker_retry\n\n"
        "@broker_retry\n"
        "def retry_external_paper_order():\n"
        "    pass\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == []
    assert _find_protected_function_offenders(tree) == [
        "decorator 'broker_retry' on retry_external_paper_order at line 3",
    ]


def test_detector_flags_an_indirect_retrying_context_manager_call(tmp_path):
    """A protected function using an indirectly-imported `Retrying(...)` as a
    context manager, never a decorator and never a literal `tenacity` import
    in this file, is still flagged."""
    offending_module = tmp_path / "synthetic_external_broker_indirect_retrying.py"
    offending_module.write_text(
        "from retry_utils import Retrying\n\n"
        "def _prepare_external_retry_attempt():\n"
        "    with Retrying(stop=None):\n"
        "        pass\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == []
    assert _find_protected_function_offenders(tree) == [
        "call to 'Retrying' inside _prepare_external_retry_attempt at line 4",
    ]


def test_detector_flags_an_indirect_decorator_on_the_shared_submission_helper(tmp_path):
    """The gap identified in PR 14 review: `retry_external_paper_order` and
    the ordinary first-attempt path both delegate to
    `_submit_checkpointed_attempt` for the actual broker call, so a wrapper
    placed there alone -- never on the three originally named functions --
    would bypass a guard that only protected those three."""
    offending_module = tmp_path / "synthetic_external_broker_indirect_submit_helper.py"
    offending_module.write_text(
        "from retry_utils import broker_retry\n\n"
        "@broker_retry\n"
        "def _submit_checkpointed_attempt():\n"
        "    pass\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == []
    assert _find_protected_function_offenders(tree) == [
        "decorator 'broker_retry' on _submit_checkpointed_attempt at line 3",
    ]


def test_detector_allows_decorators_on_unrelated_functions(tmp_path):
    """Confirms the protected-function guard does not overreach onto
    unrelated code: a decorator on a function outside `_PROTECTED_FUNCTIONS`
    is allowed, matching `DECISIONS.md` D12's scope limited to the
    ambiguous-broker-retry path, not the whole file."""
    module = tmp_path / "synthetic_external_broker_unrelated_decorator.py"
    module.write_text(
        "from retry_utils import broker_retry\n\n"
        "@broker_retry\n"
        "def some_unrelated_helper():\n"
        "    pass\n"
    )

    tree = ast.parse(module.read_text())

    assert _find_protected_function_offenders(tree) == []


def test_detector_flags_a_synthetic_dynamic_tenacity_import(tmp_path):
    """PR 14 review round 3: a dynamic `importlib.import_module("tenacity")`
    call bypasses the static `ast.Import`/`ast.ImportFrom` check; the
    detector must catch it separately."""
    offending_module = tmp_path / "synthetic_external_broker_dynamic_import.py"
    offending_module.write_text(
        "import importlib\n\n"
        "tenacity = importlib.import_module('tenacity')\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == [
        "dynamic import of tenacity at line 3",
    ]


def test_detector_flags_an_aliased_retrying_call(tmp_path):
    """PR 14 review round 3: an indirectly-imported `Retrying` aliased to an
    unrelated local name (e.g. re-exported from a project-local
    `retry_utils` module, itself backed by Tenacity elsewhere) then calling
    `broker_retrying(...)` bypassed the literal-spelling `retry`/`Retrying`
    call-name check because the call site's identifier was
    `broker_retrying`, not `Retrying`. Import-alias resolution must trace it
    back to `Retrying`. Uses a non-`tenacity` module name so this exercises
    `_find_protected_function_offenders` specifically, independent of
    `_find_tenacity_import_offenders`."""
    offending_module = tmp_path / "synthetic_external_broker_aliased_retrying.py"
    offending_module.write_text(
        "from retry_utils import Retrying as broker_retrying\n\n"
        "def refresh_retry_preview():\n"
        "    with broker_retrying(stop=None):\n"
        "        pass\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == []
    assert _find_protected_function_offenders(tree) == [
        "call to 'Retrying' inside refresh_retry_preview at line 4",
    ]


def test_detector_flags_a_retry_decorated_helper_transitively_called_by_a_protected_function(tmp_path):
    """PR 14 review round 3: a helper function never named in
    `_PROTECTED_FUNCTIONS` but delegated to (by bare-name call) from one of
    them previously went unchecked -- the AST walk over a protected
    function's own body does not descend into a sibling function's
    definition. `retry_external_paper_order` here calls `_do_submit()`,
    which itself carries a retry decorator."""
    offending_module = tmp_path / "synthetic_external_broker_transitive_helper.py"
    offending_module.write_text(
        "from retry_utils import retry\n\n"
        "@retry\n"
        "def _do_submit():\n"
        "    pass\n\n"
        "def retry_external_paper_order():\n"
        "    _do_submit()\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == []
    assert _find_protected_function_offenders(tree) == [
        "decorator 'retry' on _do_submit at line 3",
    ]


def test_detector_flags_a_retry_call_inside_a_transitively_called_helper(tmp_path):
    """Same transitive-delegation gap as above, but the helper wraps the
    call with `Retrying(...)` instead of a decorator."""
    offending_module = tmp_path / "synthetic_external_broker_transitive_helper_call.py"
    offending_module.write_text(
        "from retry_utils import Retrying\n\n"
        "def _do_submit():\n"
        "    with Retrying(stop=None):\n"
        "        pass\n\n"
        "def _submit_checkpointed_attempt():\n"
        "    _do_submit()\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == []
    assert _find_protected_function_offenders(tree) == [
        "call to 'Retrying' inside _do_submit at line 4",
    ]


def test_detector_does_not_flag_unrelated_decorators_on_transitively_called_helpers(tmp_path):
    """Guards the narrower rule applied to transitively-called helpers
    (retry-shaped decorators/calls only, not "any decorator"): real code
    already has legitimate, unrelated decorators this call graph reaches --
    e.g. `_order_lease`'s `@contextlib.contextmanager`, called directly by
    `retry_external_paper_order` and `refresh_retry_preview`. Banning any
    decorator that far out would misfire on that real code, so only
    decorators/calls resolving to `retry`/`Retrying` are flagged there."""
    module = tmp_path / "synthetic_external_broker_unrelated_helper_decorator.py"
    module.write_text(
        "import contextlib\n\n"
        "@contextlib.contextmanager\n"
        "def _order_lease():\n"
        "    yield\n\n"
        "def retry_external_paper_order():\n"
        "    with _order_lease():\n"
        "        pass\n"
    )

    tree = ast.parse(module.read_text())

    assert _find_protected_function_offenders(tree) == []


def test_detector_does_not_flag_an_arbitrarily_named_external_factory_call(tmp_path):
    """Documents the one residual, deliberately accepted gap (see
    `_find_protected_function_offenders`'s docstring): a project-local or
    third-party wrapper imported under a name unrelated to `retry`/
    `Retrying`, and never itself calling something by those names, cannot be
    distinguished from an ordinary helper call by parsing this file alone --
    doing so would require inspecting `retry_utils` (an arbitrary external
    module), which defeats this test's zero-dependency, always-runs design,
    or banning all bare imported-name calls in protected code, which would
    misfire on legitimate future helpers. This is the same category of
    limitation this module's top docstring already documents for
    indirection generally; it is not closable by this detector without a
    different enforcement mechanism entirely."""
    module = tmp_path / "synthetic_external_broker_arbitrary_factory.py"
    module.write_text(
        "from retry_utils import broker_retrying\n\n"
        "def _submit_checkpointed_attempt():\n"
        "    with broker_retrying():\n"
        "        pass\n"
    )

    tree = ast.parse(module.read_text())

    assert _find_tenacity_import_offenders(tree) == []
    assert _find_protected_function_offenders(tree) == []


def test_detector_flags_a_module_level_reassignment_of_a_protected_function(tmp_path):
    """PR 14 review round 4: `_submit_checkpointed_attempt = broker_retry(
    _submit_checkpointed_attempt)` immediately after the `def` never touches
    the function's own `decorator_list` or body -- neither the decorator
    check nor the inner-call check sees a module-level rebinding. Only
    top-level (module-body) assignments are inspected."""
    offending_module = tmp_path / "synthetic_external_broker_reassignment.py"
    offending_module.write_text(
        "from retry_utils import broker_retry\n\n"
        "def _submit_checkpointed_attempt():\n"
        "    pass\n\n"
        "_submit_checkpointed_attempt = broker_retry(_submit_checkpointed_attempt)\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == []
    assert _find_protected_function_offenders(tree) == [
        "module-level reassignment of '_submit_checkpointed_attempt' to 'broker_retry' at line 6",
    ]


def test_detector_flags_a_module_level_reassignment_of_a_transitively_called_helper(tmp_path):
    """Same reassignment gap, but on a helper reached only transitively --
    the narrower "retry-shaped only" rule still applies to reassignment, not
    the stricter "any call" rule reserved for the four named functions."""
    offending_module = tmp_path / "synthetic_external_broker_helper_reassignment.py"
    offending_module.write_text(
        "from retry_utils import retry\n\n"
        "def _do_submit():\n"
        "    pass\n\n"
        "_do_submit = retry(_do_submit)\n\n"
        "def retry_external_paper_order():\n"
        "    _do_submit()\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "module-level reassignment of '_do_submit' to 'retry' at line 6",
    ]


def test_detector_does_not_flag_an_unrelated_module_level_reassignment(tmp_path):
    """A module-level reassignment of a name outside `_PROTECTED_FUNCTIONS`
    and never transitively called by one is not a redefinition of the
    ambiguous-broker-retry path and must not be flagged."""
    module = tmp_path / "synthetic_external_broker_unrelated_reassignment.py"
    module.write_text(
        "from retry_utils import broker_retry\n\n"
        "def unrelated_helper():\n"
        "    pass\n\n"
        "unrelated_helper = broker_retry(unrelated_helper)\n"
    )

    tree = ast.parse(module.read_text())

    assert _find_protected_function_offenders(tree) == []


def test_detector_flags_an_annotated_module_level_reassignment(tmp_path):
    """PR 14 review round 5: `_submit_checkpointed_attempt: object =
    broker_retry(_submit_checkpointed_attempt)` is an `ast.AnnAssign`, not an
    `ast.Assign` -- the round-4 scan matched only the latter."""
    offending_module = tmp_path / "synthetic_external_broker_annotated_reassignment.py"
    offending_module.write_text(
        "from typing import Callable\n"
        "from retry_utils import broker_retry\n\n"
        "def _submit_checkpointed_attempt():\n"
        "    pass\n\n"
        "_submit_checkpointed_attempt: Callable = broker_retry(_submit_checkpointed_attempt)\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "module-level reassignment of '_submit_checkpointed_attempt' to 'broker_retry' at line 7",
    ]


def test_detector_flags_a_module_level_reassignment_nested_in_an_if_block(tmp_path):
    """PR 14 review round 5: the round-4 scan only inspected direct children
    of `tree.body`, so the identical reassignment wrapped in a top-level
    `if:` block (still module scope in Python -- `if` does not introduce a
    new scope) was invisible to it."""
    offending_module = tmp_path / "synthetic_external_broker_if_nested_reassignment.py"
    offending_module.write_text(
        "from retry_utils import broker_retry\n\n"
        "def _submit_checkpointed_attempt():\n"
        "    pass\n\n"
        "if True:\n"
        "    _submit_checkpointed_attempt = broker_retry(_submit_checkpointed_attempt)\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "module-level reassignment of '_submit_checkpointed_attempt' to 'broker_retry' at line 7",
    ]


def test_detector_flags_a_module_level_reassignment_nested_in_a_try_block(tmp_path):
    """Same nested-block gap as the `if` case above, for `try`/`except`."""
    offending_module = tmp_path / "synthetic_external_broker_try_nested_reassignment.py"
    offending_module.write_text(
        "from retry_utils import broker_retry\n\n"
        "def _submit_checkpointed_attempt():\n"
        "    pass\n\n"
        "try:\n"
        "    _submit_checkpointed_attempt = broker_retry(_submit_checkpointed_attempt)\n"
        "except Exception:\n"
        "    pass\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "module-level reassignment of '_submit_checkpointed_attempt' to 'broker_retry' at line 7",
    ]


def test_detector_flags_a_module_level_reassignment_nested_in_a_with_block(tmp_path):
    """Same nested-block gap as the `if` case above, for `with`."""
    offending_module = tmp_path / "synthetic_external_broker_with_nested_reassignment.py"
    offending_module.write_text(
        "import contextlib\n"
        "from retry_utils import broker_retry\n\n"
        "def _submit_checkpointed_attempt():\n"
        "    pass\n\n"
        "with contextlib.suppress(Exception):\n"
        "    _submit_checkpointed_attempt = broker_retry(_submit_checkpointed_attempt)\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "module-level reassignment of '_submit_checkpointed_attempt' to 'broker_retry' at line 8",
    ]


def test_detector_flags_a_walrus_module_level_reassignment(tmp_path):
    """PR 14 review round 5: a bare walrus expression statement
    (`(_submit_checkpointed_attempt := broker_retry(...))`) is valid Python
    that rebinds the module-level name but is wrapped in `ast.Expr`, not
    `ast.Assign` -- the round-4 scan required the latter."""
    offending_module = tmp_path / "synthetic_external_broker_walrus_reassignment.py"
    offending_module.write_text(
        "from retry_utils import broker_retry\n\n"
        "def _submit_checkpointed_attempt():\n"
        "    pass\n\n"
        "(_submit_checkpointed_attempt := broker_retry(_submit_checkpointed_attempt))\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "module-level reassignment of '_submit_checkpointed_attempt' to 'broker_retry' at line 6",
    ]


def test_detector_does_not_flag_a_same_named_local_rebind_inside_an_unrelated_function(tmp_path):
    """Guards against overreach from the round-5 nested-block recursion: a
    same-named local variable reassigned inside an unrelated function's own
    body (a real new scope, unlike `if`/`try`/`with`) is not a redefinition
    of the module-level protected name and must not be flagged."""
    module = tmp_path / "synthetic_external_broker_function_scoped_rebind.py"
    module.write_text(
        "from retry_utils import broker_retry\n\n"
        "def _submit_checkpointed_attempt():\n"
        "    pass\n\n"
        "def unrelated_helper():\n"
        "    _submit_checkpointed_attempt = broker_retry(_submit_checkpointed_attempt)\n"
        "    return _submit_checkpointed_attempt\n"
    )

    tree = ast.parse(module.read_text())

    assert _find_protected_function_offenders(tree) == []


def test_detector_flags_a_functools_partial_wrapped_retry_decorator(tmp_path):
    """PR 14 review round 5: `@functools.partial(retry, stop=3)` resolves
    its own call-name to `partial`, one level short of the retry-shaped
    callable it wraps, bypassing the round-3 decorator check."""
    offending_module = tmp_path / "synthetic_external_broker_partial_decorator.py"
    offending_module.write_text(
        "import functools\n"
        "from retry_utils import retry\n\n"
        "@functools.partial(retry, stop=3)\n"
        "def _submit_checkpointed_attempt():\n"
        "    pass\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "decorator 'retry' on _submit_checkpointed_attempt at line 4",
    ]


def test_detector_does_not_flag_an_unrelated_functools_partial_decorator(tmp_path):
    """Guards against overreach: `functools.partial` wrapping something
    other than `retry`/`Retrying` must not be flagged."""
    module = tmp_path / "synthetic_external_broker_unrelated_partial_decorator.py"
    module.write_text(
        "import functools\n"
        "from retry_utils import some_unrelated_factory\n\n"
        "@functools.partial(some_unrelated_factory, stop=3)\n"
        "def retry_external_paper_order():\n"
        "    pass\n"
    )

    tree = ast.parse(module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "decorator 'partial' on retry_external_paper_order at line 4",
    ]


def test_detector_flags_an_aliased_dynamic_import_of_tenacity(tmp_path):
    """PR 14 review round 4: `from importlib import import_module as load`
    then `load("tenacity")` bypassed the dynamic-import check because it
    compared the call's literal spelling (`load`) against
    `{"import_module", "__import__"}` without resolving the import alias
    first."""
    offending_module = tmp_path / "synthetic_external_broker_aliased_dynamic_import.py"
    offending_module.write_text(
        "from importlib import import_module as load\n\n"
        "tenacity = load('tenacity')\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == [
        "dynamic import of tenacity at line 3",
    ]


def test_detector_flags_a_dynamic_import_of_tenacity_via_keyword_argument(tmp_path):
    """PR 14 review round 4: `importlib.import_module(name="tenacity")`
    bypassed the dynamic-import check because only positional `node.args`
    was scanned for the target module name, not `name=` keyword arguments,
    which both `import_module` and `__import__` accept."""
    offending_module = tmp_path / "synthetic_external_broker_keyword_dynamic_import.py"
    offending_module.write_text(
        "import importlib\n\n"
        "tenacity = importlib.import_module(name='tenacity')\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == [
        "dynamic import of tenacity at line 3",
    ]


def test_detector_does_not_flag_an_aliased_dynamic_import_of_an_unrelated_module(tmp_path):
    """Guards against overreach: an aliased `import_module`/`__import__`
    call for a module other than `tenacity` must not be flagged."""
    module = tmp_path / "synthetic_external_broker_aliased_unrelated_import.py"
    module.write_text(
        "from importlib import import_module as load\n\n"
        "numpy = load('numpy')\n"
    )

    tree = ast.parse(module.read_text())

    assert _find_tenacity_import_offenders(tree) == []


def test_detector_flags_a_module_level_reassignment_nested_in_a_for_block(tmp_path):
    """PR 14 review round 6: the round-5 nested-block recursion only handled
    `if`/`try`/`with`, so the identical reassignment wrapped in a top-level
    `for:` block (also not a new scope in Python) was invisible to it."""
    offending_module = tmp_path / "synthetic_external_broker_for_nested_reassignment.py"
    offending_module.write_text(
        "from retry_utils import broker_retry\n\n"
        "def _submit_checkpointed_attempt():\n"
        "    pass\n\n"
        "for _ in range(1):\n"
        "    _submit_checkpointed_attempt = broker_retry(_submit_checkpointed_attempt)\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "module-level reassignment of '_submit_checkpointed_attempt' to 'broker_retry' at line 7",
    ]


def test_detector_flags_a_module_level_reassignment_nested_in_a_while_block(tmp_path):
    """Same nested-block gap as the `for` case above, for `while`."""
    offending_module = tmp_path / "synthetic_external_broker_while_nested_reassignment.py"
    offending_module.write_text(
        "from retry_utils import broker_retry\n\n"
        "def _submit_checkpointed_attempt():\n"
        "    pass\n\n"
        "_ready = True\n"
        "while _ready:\n"
        "    _submit_checkpointed_attempt = broker_retry(_submit_checkpointed_attempt)\n"
        "    _ready = False\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "module-level reassignment of '_submit_checkpointed_attempt' to 'broker_retry' at line 8",
    ]


def test_detector_flags_a_module_level_reassignment_nested_in_an_async_for_block(tmp_path):
    """Same nested-block gap as the `for` case above, for `async for` -- not
    executable at true module scope, but still parseable source `ast.parse`
    must not silently pass over, matching this detector's parse-only,
    zero-dependency design (it never executes the file it inspects)."""
    offending_module = tmp_path / "synthetic_external_broker_async_for_nested_reassignment.py"
    offending_module.write_text(
        "from retry_utils import broker_retry\n\n"
        "def _submit_checkpointed_attempt():\n"
        "    pass\n\n"
        "async for _ in _aiter():\n"
        "    _submit_checkpointed_attempt = broker_retry(_submit_checkpointed_attempt)\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "module-level reassignment of '_submit_checkpointed_attempt' to 'broker_retry' at line 7",
    ]


def test_detector_flags_a_composed_functools_partial_call_reassignment_of_a_transitively_called_helper(
    tmp_path,
):
    """PR 14 review round 6: `_do_submit = functools.partial(retry, stop=3)
    (_do_submit)` composes the retry wrapper through `functools.partial(...)`
    on the reassignment's right-hand side, rather than decorating. The callee
    of the outer call (`value.func`) is itself the `functools.partial(retry,
    stop=3)` call, so the round-4/5 reassignment check -- which only resolved
    `value.func`'s own name (`partial`'s callable, not what it wraps) --
    missed it. Uses a transitively-called helper (the narrower "retry-shaped
    only" rule) so this exercises the reassignment-callee unwrap
    specifically, independent of the `is_named_protected` shortcut that
    already covers the four named functions regardless of call shape."""
    offending_module = tmp_path / "synthetic_external_broker_partial_call_reassignment.py"
    offending_module.write_text(
        "import functools\n"
        "from retry_utils import retry\n\n"
        "def _do_submit():\n"
        "    pass\n\n"
        "_do_submit = functools.partial(retry, stop=3)(_do_submit)\n\n"
        "def retry_external_paper_order():\n"
        "    _do_submit()\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "module-level reassignment of '_do_submit' to 'retry' at line 7",
    ]


def test_detector_does_not_flag_an_unrelated_composed_functools_partial_call_reassignment(tmp_path):
    """Guards against overreach: a reassignment composed through
    `functools.partial` wrapping something other than `retry`/`Retrying`,
    on a name outside `_PROTECTED_FUNCTIONS` and not transitively called by
    one, must not be flagged."""
    module = tmp_path / "synthetic_external_broker_unrelated_partial_call_reassignment.py"
    module.write_text(
        "import functools\n"
        "from retry_utils import some_unrelated_factory\n\n"
        "def unrelated_helper():\n"
        "    pass\n\n"
        "unrelated_helper = functools.partial(some_unrelated_factory, stop=3)(unrelated_helper)\n"
    )

    tree = ast.parse(module.read_text())

    assert _find_protected_function_offenders(tree) == []


def test_detector_flags_a_module_scope_name_to_name_aliased_decorator(tmp_path):
    """PR 14 review round 7: `broker_retry = retry` after `from retry_utils
    import retry` is a plain module-scope rebind of an already-resolved
    retry-shaped name to a new local name -- not an `import ... as ...`
    alias, so the round-3 alias resolver never saw it, letting `@broker_retry`
    on a transitively-called helper bypass the decorator check."""
    offending_module = tmp_path / "synthetic_external_broker_assignment_aliased_decorator.py"
    offending_module.write_text(
        "from retry_utils import retry\n\n"
        "broker_retry = retry\n\n"
        "@broker_retry\n"
        "def _do_submit():\n"
        "    pass\n\n"
        "def retry_external_paper_order():\n"
        "    _do_submit()\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == []
    assert _find_protected_function_offenders(tree) == [
        "decorator 'retry' on _do_submit at line 5",
    ]


def test_detector_flags_a_module_scope_name_to_name_aliased_retrying_call(tmp_path):
    """Same assignment-aliasing gap as above, but for a `Retrying(...)`
    context-manager call inside a protected function instead of a
    decorator."""
    offending_module = tmp_path / "synthetic_external_broker_assignment_aliased_call.py"
    offending_module.write_text(
        "from retry_utils import Retrying\n\n"
        "broker_retrying = Retrying\n\n"
        "def refresh_retry_preview():\n"
        "    with broker_retrying(stop=None):\n"
        "        pass\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "call to 'Retrying' inside refresh_retry_preview at line 6",
    ]


def test_detector_flags_a_chained_name_to_name_aliased_decorator(tmp_path):
    """The assignment-alias resolution chains through more than one
    same-file rebind (`intermediate = retry` then `broker_retry =
    intermediate`), not just a single hop."""
    offending_module = tmp_path / "synthetic_external_broker_chained_assignment_alias.py"
    offending_module.write_text(
        "from retry_utils import retry\n\n"
        "intermediate = retry\n"
        "broker_retry = intermediate\n\n"
        "@broker_retry\n"
        "def _do_submit():\n"
        "    pass\n\n"
        "def retry_external_paper_order():\n"
        "    _do_submit()\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "decorator 'retry' on _do_submit at line 6",
    ]


def test_detector_flags_a_name_to_name_aliased_decorator_nested_in_an_if_block(tmp_path):
    """The assignment-alias rebind is still visible when nested one level
    inside a top-level `if:` block -- true module scope in Python, matching
    the scope boundary `_rebind_offenders_in_block` already enforces for
    protected-function reassignment."""
    offending_module = tmp_path / "synthetic_external_broker_if_nested_assignment_alias.py"
    offending_module.write_text(
        "from retry_utils import retry\n\n"
        "if True:\n"
        "    broker_retry = retry\n\n"
        "@broker_retry\n"
        "def _do_submit():\n"
        "    pass\n\n"
        "def retry_external_paper_order():\n"
        "    _do_submit()\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "decorator 'retry' on _do_submit at line 6",
    ]


def test_detector_does_not_flag_an_unrelated_name_to_name_assignment_alias(tmp_path):
    """Guards against overreach: a module-scope name-to-name rebind of a
    name unrelated to `retry`/`Retrying`, used as a decorator on an
    unrelated function, must not be flagged."""
    module = tmp_path / "synthetic_external_broker_unrelated_assignment_alias.py"
    module.write_text(
        "from retry_utils import some_unrelated_factory\n\n"
        "aliased_factory = some_unrelated_factory\n\n"
        "@aliased_factory\n"
        "def some_unrelated_helper():\n"
        "    pass\n"
    )

    tree = ast.parse(module.read_text())

    assert _find_protected_function_offenders(tree) == []


def test_detector_does_not_flag_a_same_named_local_assignment_alias_inside_an_unrelated_function(tmp_path):
    """Guards against overreach from the assignment-alias resolver: a
    name-to-name rebind inside an unrelated function's own body (a real new
    scope, unlike `if`/`try`/`with`/`for`/`while`) must not be treated as a
    module-scope alias."""
    module = tmp_path / "synthetic_external_broker_function_scoped_assignment_alias.py"
    module.write_text(
        "from retry_utils import retry\n\n"
        "def unrelated_helper():\n"
        "    broker_retry = retry\n"
        "    return broker_retry\n\n"
        "@broker_retry\n"
        "def _do_submit():\n"
        "    pass\n\n"
        "def retry_external_paper_order():\n"
        "    _do_submit()\n"
    )

    tree = ast.parse(module.read_text())

    assert _find_protected_function_offenders(tree) == []


def test_detector_flags_a_module_level_reassignment_nested_in_a_match_case(tmp_path):
    """PR 14 review round 8: a `match` statement's `case` bodies do not
    introduce a new scope, like `if`/`try`/`with`/`for`/`while` before it,
    but `_rebind_offenders_in_block` never recursed into them, so a
    reassignment of a protected name to a retry-wrapper call hidden inside
    a `case` block previously bypassed the guard entirely."""
    offending_module = tmp_path / "synthetic_external_broker_match_case_reassignment.py"
    offending_module.write_text(
        "from retry_utils import broker_retry\n\n"
        "def _submit_checkpointed_attempt():\n"
        "    pass\n\n"
        "match 1:\n"
        "    case _:\n"
        "        _submit_checkpointed_attempt = broker_retry(_submit_checkpointed_attempt)\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "module-level reassignment of '_submit_checkpointed_attempt' to 'broker_retry' at line 8",
    ]


@pytest.mark.skipif(sys.version_info < (3, 11), reason="except* (PEP 654) requires Python 3.11+")
def test_detector_flags_a_module_level_reassignment_nested_in_an_except_star_block(tmp_path):
    """PR 14 review round 8: `except*` (`ast.TryStar`, PEP 654) is a
    non-scope-introducing block distinct from `ast.Try`, which the round-4
    reassignment scan (and the round-7 alias resolver's
    `_module_scope_statements`) never checked for, so a reassignment of a
    protected name to a retry-wrapper call hidden inside an `except*`
    handler previously bypassed the guard entirely."""
    offending_module = tmp_path / "synthetic_external_broker_except_star_reassignment.py"
    offending_module.write_text(
        "from retry_utils import broker_retry\n\n"
        "def _submit_checkpointed_attempt():\n"
        "    pass\n\n"
        "try:\n"
        "    pass\n"
        "except* Exception:\n"
        "    _submit_checkpointed_attempt = broker_retry(_submit_checkpointed_attempt)\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "module-level reassignment of '_submit_checkpointed_attempt' to 'broker_retry' at line 9",
    ]


def test_detector_flags_a_retry_decorated_helper_called_only_through_a_local_alias(tmp_path):
    """PR 14 review round 8: `_direct_local_calls` previously matched only
    the callee's own bare name against `local_functions`, so aliasing a
    retry-decorated helper to a new local name (`submit = _do_submit`) and
    calling the alias (`submit()`) from a protected function broke the
    call-graph edge `_transitively_called_local_helpers` relies on --
    making `_do_submit` unreachable, so its own `@retry` decorator was
    never even inspected despite being called, indirectly, by
    `retry_external_paper_order`."""
    offending_module = tmp_path / "synthetic_external_broker_aliased_helper_call.py"
    offending_module.write_text(
        "from retry_utils import retry\n\n"
        "@retry\n"
        "def _do_submit():\n"
        "    pass\n\n"
        "submit = _do_submit\n\n"
        "def retry_external_paper_order():\n"
        "    submit()\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "decorator 'retry' on _do_submit at line 3",
    ]


def test_detector_does_not_flag_an_unrelated_local_alias_of_a_non_retry_helper(tmp_path):
    """Guards against overreach from the round-8 alias-resolved call-graph
    edge: aliasing and calling an ordinary, undecorated helper must not
    fabricate an offender."""
    module = tmp_path / "synthetic_external_broker_unrelated_aliased_helper_call.py"
    module.write_text(
        "def _do_submit():\n"
        "    pass\n\n"
        "submit = _do_submit\n\n"
        "def retry_external_paper_order():\n"
        "    submit()\n"
    )

    tree = ast.parse(module.read_text())

    assert _find_protected_function_offenders(tree) == []


def test_detector_flags_a_retry_decorated_helper_defined_inside_an_if_block(tmp_path):
    """PR 14 review round 9: `_module_level_functions` previously scanned
    only direct children of `tree.body`, so a retry-decorated helper defined
    one level inside a top-level `if:` block (still module scope in Python --
    `if` does not introduce a new scope) was absent from the function catalog
    `_transitively_called_local_helpers` builds its call graph from. A
    protected function delegating to such a helper therefore never made it
    reachable, so the helper's own `@retry` decorator was never inspected."""
    offending_module = tmp_path / "synthetic_external_broker_if_nested_helper_def.py"
    offending_module.write_text(
        "from retry_utils import retry\n\n"
        "if True:\n"
        "    @retry\n"
        "    def _do_submit():\n"
        "        pass\n\n"
        "def _submit_checkpointed_attempt():\n"
        "    _do_submit()\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_tenacity_import_offenders(tree) == []
    assert _find_protected_function_offenders(tree) == [
        "decorator 'retry' on _do_submit at line 4",
    ]


def test_detector_flags_a_retry_decorated_helper_defined_inside_a_match_case(tmp_path):
    """Same nested-definition gap as the `if` case above, for a `match`
    statement's `case` body -- also not a new scope in Python, and already
    recursed into by `_module_scope_statements` for aliases and
    reassignments, but `_module_level_functions` did not share that
    traversal before this fix."""
    offending_module = tmp_path / "synthetic_external_broker_match_case_helper_def.py"
    offending_module.write_text(
        "from retry_utils import retry\n\n"
        "match 1:\n"
        "    case _:\n"
        "        @retry\n"
        "        def _do_submit():\n"
        "            pass\n\n"
        "def retry_external_paper_order():\n"
        "    _do_submit()\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "decorator 'retry' on _do_submit at line 5",
    ]


def test_detector_does_not_treat_a_closure_as_a_module_level_function(tmp_path):
    """Guards against overreach from the round-9 `_module_level_functions`
    recursion: a function nested inside another function's own body is a
    real new scope, unlike `if`/`try`/`with`/`for`/`while`/`match`, and must
    never be treated as a module-level definition -- matching the
    already-documented residual gap that closures nested inside a protected
    function's own body are outside this call-graph analysis entirely."""
    module = tmp_path / "synthetic_external_broker_closure_not_module_level.py"
    module.write_text(
        "from retry_utils import retry\n\n"
        "def retry_external_paper_order():\n"
        "    @retry\n"
        "    def _inner():\n"
        "        pass\n"
        "    _inner()\n"
    )

    tree = ast.parse(module.read_text())

    assert _find_protected_function_offenders(tree) == []


def test_detector_flags_a_retry_decorated_helper_called_only_through_a_function_local_alias(tmp_path):
    """PR 14 review round 9: the round-8 fix resolved a call's bare name
    through *module-scope* aliases only. A same-named alias assigned inside
    the calling function's own body (`submit = _do_submit` followed by
    `submit()`, both inside `retry_external_paper_order`) broke the
    call-graph edge to `_do_submit` the same way the round-8 module-scope
    gap did, leaving its `@retry` decorator uninspected despite being called,
    indirectly, by a protected function."""
    offending_module = tmp_path / "synthetic_external_broker_function_local_aliased_helper_call.py"
    offending_module.write_text(
        "from retry_utils import retry\n\n"
        "@retry\n"
        "def _do_submit():\n"
        "    pass\n\n"
        "def retry_external_paper_order():\n"
        "    submit = _do_submit\n"
        "    submit()\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "decorator 'retry' on _do_submit at line 3",
    ]


def test_detector_does_not_flag_an_unrelated_function_local_alias_of_a_non_retry_helper(tmp_path):
    """Guards against overreach from the round-9 function-local alias
    resolution: aliasing and calling an ordinary, undecorated helper inside a
    protected function's own body must not fabricate an offender."""
    module = tmp_path / "synthetic_external_broker_unrelated_function_local_aliased_call.py"
    module.write_text(
        "def _do_submit():\n"
        "    pass\n\n"
        "def retry_external_paper_order():\n"
        "    submit = _do_submit\n"
        "    submit()\n"
    )

    tree = ast.parse(module.read_text())

    assert _find_protected_function_offenders(tree) == []


def test_detector_flags_a_module_scope_branch_dependent_aliased_decorator(tmp_path):
    """PR 14 review round 10: `_resolve_import_aliases` previously folded
    every visited assignment into one unconditional `dict[str, str]`, so
    `wrapper = retry` on one branch of a module-scope `if`/`else` could be
    silently overwritten by `wrapper = ordinary` on the other branch, even
    though the retry-wrapped path remains executable whenever the `if`
    branch is taken. `@wrapper` on a transitively-called helper must still
    be flagged."""
    offending_module = tmp_path / "synthetic_external_broker_branch_dependent_module_alias.py"
    offending_module.write_text(
        "from retry_utils import retry\n\n"
        "def ordinary(func):\n"
        "    return func\n\n"
        "if enabled:\n"
        "    wrapper = retry\n"
        "else:\n"
        "    wrapper = ordinary\n\n"
        "@wrapper\n"
        "def _do_submit():\n"
        "    pass\n\n"
        "def retry_external_paper_order():\n"
        "    _do_submit()\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "decorator 'retry' on _do_submit at line 11",
    ]


def test_detector_flags_a_module_scope_branch_dependent_aliased_decorator_other_branch_order(tmp_path):
    """Same branch-dependent alias gap as above, with the retry binding on
    the `else` branch instead of the `if` branch, proving the fix does not
    merely prefer whichever branch happens to be visited first."""
    offending_module = tmp_path / "synthetic_external_broker_branch_dependent_module_alias_else.py"
    offending_module.write_text(
        "from retry_utils import retry\n\n"
        "def ordinary(func):\n"
        "    return func\n\n"
        "if enabled:\n"
        "    wrapper = ordinary\n"
        "else:\n"
        "    wrapper = retry\n\n"
        "@wrapper\n"
        "def _do_submit():\n"
        "    pass\n\n"
        "def retry_external_paper_order():\n"
        "    _do_submit()\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "decorator 'retry' on _do_submit at line 11",
    ]


def test_detector_does_not_flag_a_module_scope_branch_dependent_alias_when_no_branch_is_retry_shaped(
    tmp_path,
):
    """Guards against overreach from the branch-aware alias merge: when
    neither feasible branch binding of a module-scope alias is retry-shaped,
    the decorator must not be flagged."""
    module = tmp_path / "synthetic_external_broker_branch_dependent_module_alias_negative.py"
    module.write_text(
        "def ordinary_one(func):\n"
        "    return func\n\n"
        "def ordinary_two(func):\n"
        "    return func\n\n"
        "if enabled:\n"
        "    wrapper = ordinary_one\n"
        "else:\n"
        "    wrapper = ordinary_two\n\n"
        "@wrapper\n"
        "def _do_submit():\n"
        "    pass\n\n"
        "def retry_external_paper_order():\n"
        "    _do_submit()\n"
    )

    tree = ast.parse(module.read_text())

    assert _find_protected_function_offenders(tree) == []


def test_detector_flags_a_retry_decorated_helper_called_through_an_annotated_function_local_alias(
    tmp_path,
):
    """PR 14 review round 10: `_local_aliases_in_block` previously matched
    only a bare, unannotated `ast.Assign`, so a type-annotated local alias
    (`submit: object = _do_submit`) left `_do_submit` unresolved by
    `_direct_local_calls`, making it unreachable from the protected
    function that called it only through `submit()`."""
    offending_module = tmp_path / "synthetic_external_broker_annotated_local_alias.py"
    offending_module.write_text(
        "from retry_utils import retry\n\n"
        "@retry\n"
        "def _do_submit():\n"
        "    pass\n\n"
        "def retry_external_paper_order():\n"
        "    submit: object = _do_submit\n"
        "    submit()\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "decorator 'retry' on _do_submit at line 3",
    ]


def test_detector_flags_a_retry_decorated_helper_called_through_a_branch_dependent_local_alias(
    tmp_path,
):
    """PR 14 review round 10: like the module-scope gap, `_local_aliases_in_
    block` previously collapsed a function-local alias bound differently on
    two mutually exclusive branches (`if enabled: submit = _do_submit / else:
    submit = ordinary`) to whichever branch's assignment was visited last,
    losing the call-graph edge to `_do_submit` whenever that branch was the
    `else`."""
    offending_module = tmp_path / "synthetic_external_broker_branch_dependent_local_alias.py"
    offending_module.write_text(
        "from retry_utils import retry\n\n"
        "@retry\n"
        "def _do_submit():\n"
        "    pass\n\n"
        "def ordinary():\n"
        "    pass\n\n"
        "def retry_external_paper_order():\n"
        "    if enabled:\n"
        "        submit = _do_submit\n"
        "    else:\n"
        "        submit = ordinary\n"
        "    submit()\n"
    )

    tree = ast.parse(offending_module.read_text())

    assert _find_protected_function_offenders(tree) == [
        "decorator 'retry' on _do_submit at line 3",
    ]


def test_detector_does_not_flag_a_branch_dependent_local_alias_when_no_branch_is_retry_decorated(
    tmp_path,
):
    """Guards against overreach from the branch-aware local alias merge:
    when neither feasible branch binding of a function-local alias resolves
    to a retry-decorated helper, calling through that alias must not
    fabricate an offender."""
    module = tmp_path / "synthetic_external_broker_branch_dependent_local_alias_negative.py"
    module.write_text(
        "def _do_submit():\n"
        "    pass\n\n"
        "def ordinary():\n"
        "    pass\n\n"
        "def retry_external_paper_order():\n"
        "    if enabled:\n"
        "        submit = _do_submit\n"
        "    else:\n"
        "        submit = ordinary\n"
        "    submit()\n"
    )

    tree = ast.parse(module.read_text())

    assert _find_protected_function_offenders(tree) == []
