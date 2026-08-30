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
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "tenacity" for a in node.names):
            offenders.append(f"import tenacity at line {node.lineno}")
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "tenacity":
            offenders.append(f"from tenacity import ... at line {node.lineno}")
    return offenders


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _find_protected_function_offenders(tree: ast.Module) -> list[str]:
    """Flags any decorator, or any call named `retry`/`Retrying`, attached to
    or used inside one of `_PROTECTED_FUNCTIONS` -- regardless of what module
    that name was imported from. This closes the indirect-wrapper gap that
    `_find_tenacity_import_offenders` alone cannot: a project-local name that
    is itself backed by Tenacity elsewhere never triggers a literal `tenacity`
    import node in this file.
    """
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in _PROTECTED_FUNCTIONS:
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            name = _call_name(target) or ast.dump(target)
            offenders.append(f"decorator {name!r} on {node.name} at line {decorator.lineno}")
        for statement in node.body:
            for inner in ast.walk(statement):
                if isinstance(inner, ast.Call):
                    name = _call_name(inner.func)
                    if name in _RETRY_WRAPPER_CALL_NAMES:
                        offenders.append(f"call to {name!r} inside {node.name} at line {inner.lineno}")
    return offenders


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
