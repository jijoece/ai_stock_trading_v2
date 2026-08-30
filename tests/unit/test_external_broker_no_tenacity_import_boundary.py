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
        if isinstance(node, ast.Call) and _resolved_call_name(node.func, aliases) in {"import_module", "__import__"}:
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


def _resolve_import_aliases(tree: ast.Module) -> dict[str, str]:
    """Maps each locally-used name to the name it was imported as, so
    `broker_retrying` from `from tenacity import Retrying as broker_retrying`
    still resolves to `Retrying` for the retry-wrapper-call check -- closing
    the aliased-import gap a literal-spelling comparison alone missed (PR 14
    review round 3)."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name.split(".")[-1]
    return aliases


def _resolved_call_name(node: ast.expr, aliases: dict[str, str]) -> str | None:
    name = _call_name(node)
    if name is None:
        return None
    return aliases.get(name, name)


def _module_level_functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _direct_local_calls(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    local_functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
) -> set[str]:
    """Bare-name calls only (`helper(...)`), never attribute calls
    (`repo.helper(...)`), so an unrelated method that happens to share a
    local function's name can never fabricate a call-graph edge."""
    called = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id in local_functions:
            called.add(inner.func.id)
    return called


def _transitively_called_local_helpers(
    tree: ast.Module, entry_points: frozenset[str],
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """The module-level functions in this file reachable (directly or
    indirectly, by bare-name call) from `entry_points`, excluding the entry
    points themselves. Closes the "retry-decorated helper transitively
    called by the broker-call boundary" gap: a wrapper need not sit on one
    of the four named functions directly if it can instead sit on a helper
    one of them delegates to (PR 14 review round 3)."""
    local_functions = _module_level_functions(tree)
    reachable: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    seen = set(entry_points)
    frontier = set(entry_points)
    while frontier:
        name = frontier.pop()
        node = local_functions.get(name)
        if node is None:
            continue
        for callee in _direct_local_calls(node, local_functions):
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

    Helpers reached only transitively are checked narrowly -- retry-shaped
    decorators/calls/reassignments only, not "any decorator" -- because
    legitimate, unrelated decorators already exist on functions this path
    reaches (e.g. `_order_lease`'s `@contextlib.contextmanager`, called
    directly by `retry_external_paper_order` and `refresh_retry_preview`);
    banning any decorator that far out would misfire on real code. The four
    functions named in `_PROTECTED_FUNCTIONS` keep the stricter "any
    decorator"/"any reassignment" rule since the master plan names them
    directly and none legitimately carries either form today.

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
    helpers = _transitively_called_local_helpers(tree, _PROTECTED_FUNCTIONS)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_named_protected = node.name in _PROTECTED_FUNCTIONS
        if not is_named_protected and node.name not in helpers:
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            name = _resolved_call_name(target, aliases) or ast.dump(target)
            if name == "partial" and isinstance(decorator, ast.Call):
                # PR 14 review round 5: `functools.partial(retry, ...)` used
                # as a decorator resolves its own call-name to `partial`, one
                # level short of the retry-shaped callable it wraps.
                wrapped = list(decorator.args[:1]) + [
                    kw.value for kw in decorator.keywords if kw.arg == "func"
                ]
                for candidate in wrapped:
                    wrapped_name = _resolved_call_name(candidate, aliases)
                    if wrapped_name in _RETRY_WRAPPER_CALL_NAMES:
                        name = wrapped_name
                        break
            if is_named_protected or name in _RETRY_WRAPPER_CALL_NAMES:
                offenders.append(f"decorator {name!r} on {node.name} at line {decorator.lineno}")
        for statement in node.body:
            for inner in ast.walk(statement):
                if isinstance(inner, ast.Call):
                    name = _resolved_call_name(inner.func, aliases)
                    if name in _RETRY_WRAPPER_CALL_NAMES:
                        offenders.append(f"call to {name!r} inside {node.name} at line {inner.lineno}")
    offenders.extend(_rebind_offenders_in_block(tree.body, helpers, aliases))
    return offenders


def _rebind_offenders_in_block(
    statements: list[ast.stmt],
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    aliases: dict[str, str],
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
    wrapped in `ast.Expr`, e.g. `(_do_submit := retry(_do_submit))`)."""
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
        elif isinstance(statement, ast.Try):
            offenders.extend(_rebind_offenders_in_block(statement.body, helpers, aliases))
            for handler in statement.handlers:
                offenders.extend(_rebind_offenders_in_block(handler.body, helpers, aliases))
            offenders.extend(_rebind_offenders_in_block(statement.orelse, helpers, aliases))
            offenders.extend(_rebind_offenders_in_block(statement.finalbody, helpers, aliases))
        elif isinstance(statement, (ast.With, ast.AsyncWith)):
            offenders.extend(_rebind_offenders_in_block(statement.body, helpers, aliases))
    return offenders


def _rebind_offender(
    target: ast.Name,
    value: ast.Call,
    helpers: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    aliases: dict[str, str],
    lineno: int,
) -> list[str]:
    is_named_protected = target.id in _PROTECTED_FUNCTIONS
    if not is_named_protected and target.id not in helpers:
        return []
    name = _resolved_call_name(value.func, aliases) or ast.dump(value.func)
    if is_named_protected or name in _RETRY_WRAPPER_CALL_NAMES:
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
