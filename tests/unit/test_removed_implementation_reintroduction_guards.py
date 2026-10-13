"""Library-migration PR 18: structural guards that keep the custom market
calendar (removed in PR 3) and the custom logging formatters (removed in
PR 15) from being reintroduced (`docs/milestones/rebuild/plan.md`
completion criterion 11, `docs/library-migration/DECISIONS.md` D16).

PR 3 and PR 15 closed their `REMOVAL_MANIFEST.md` rows in place, and their
behavioral tests (`test_market_calendar.py`, `test_logging_config.py`)
prove the library-backed behavior. Neither added a check that fails when
the deleted code comes back beside the library, which is what this file
adds. It follows the PR 17 analytics guard
(`tests/unit/test_analytics_import_boundary.py`) and proves by
construction that:

1. `exchange_calendars` is imported only by `evaluation/market_calendar.py`,
   no other calendar/holiday library is imported, none of the removed
   holiday-arithmetic helpers or the fixed open/close constants are defined
   anywhere, and every XNYS-dependent public function still consults the
   cached XNYS calendar;
2. `RedactingFormatter`/`JsonRedactingFormatter` are not defined anywhere,
   no `logging.Formatter` subclass exists, `structlog` and handler
   formatter installation live only in `logging_config.py`, and
   `redact`/`register_secret` have exactly one definition.

Runs unconditionally: pure `ast` source parsing, no library import.
"""
from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "trading_research"
SCRIPTS_ROOT = REPO_ROOT / "scripts"
CALENDAR_PATH = SRC_ROOT / "evaluation" / "market_calendar.py"
LOGGING_PATH = SRC_ROOT / "logging_config.py"

# PR 3 deleted these from `market_calendar.py` (`REMOVAL_MANIFEST.md`).
_REMOVED_CALENDAR_HELPERS = frozenset({
    "_nth_weekday_of_month", "_last_weekday_of_month", "_easter_sunday", "_observed", "_federal_holidays",
})
_REMOVED_FIXED_SESSION_CONSTANTS = frozenset({"MARKET_OPEN_TIME", "MARKET_CLOSE_TIME"})
_ALTERNATIVE_CALENDAR_MODULES = frozenset({
    "holidays", "pandas_market_calendars", "trading_calendars", "workalendar", "pandas.tseries.holiday",
})
_XNYS_DEPENDENT_FUNCTIONS = (
    "is_trading_day", "next_trading_session", "add_trading_days", "is_market_open", "regular_session_close",
)

# PR 15 deleted these from `logging_config.py` (`REMOVAL_MANIFEST.md`).
_REMOVED_FORMATTER_CLASSES = frozenset({"RedactingFormatter", "JsonRedactingFormatter"})


def _python_sources() -> list[pathlib.Path]:
    return sorted([*SRC_ROOT.rglob("*.py"), *SCRIPTS_ROOT.rglob("*.py")])


def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _imported_modules(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _imports(tree: ast.Module, module: str) -> bool:
    return any(name == module or name.startswith(f"{module}.") for name in _imported_modules(tree))


def _defined_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"evaluation/market_calendar.py no longer defines {name}")


def _called_names(node: ast.AST) -> set[str]:
    return {n.func.id for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def _attribute_calls(node: ast.AST) -> set[str]:
    return {n.func.attr for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}


def _base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Attribute):
        return base.attr
    if isinstance(base, ast.Name):
        return base.id
    return ""


# --- Market calendar (PR 3) -------------------------------------------------


def test_exchange_calendars_only_imported_by_market_calendar():
    offenders = [
        str(path) for path in _python_sources()
        if path != CALENDAR_PATH and _imports(_tree(path), "exchange_calendars")
    ]
    assert offenders == []
    assert _imports(_tree(CALENDAR_PATH), "exchange_calendars")


def test_no_alternative_calendar_or_holiday_library_is_imported():
    offenders = [
        f"{path}: {module}" for path in _python_sources()
        for module in sorted(_ALTERNATIVE_CALENDAR_MODULES) if _imports(_tree(path), module)
    ]
    assert offenders == []


def test_removed_holiday_arithmetic_is_not_reintroduced_anywhere():
    offenders = [
        f"{path}: {name}" for path in _python_sources()
        for name in sorted(_defined_names(_tree(path)) & (_REMOVED_CALENDAR_HELPERS | _REMOVED_FIXED_SESSION_CONSTANTS))
    ]
    assert offenders == []


def test_xnys_dependent_functions_consult_the_cached_xnys_calendar():
    tree = _tree(CALENDAR_PATH)
    for name in _XNYS_DEPENDENT_FUNCTIONS:
        assert "_calendar" in _called_names(_function(tree, name)), f"{name} no longer consults XNYS"
    assert "get_calendar" in _called_names(_function(tree, "_calendar"))


# --- Logging formatter (PR 15) ----------------------------------------------


def test_removed_formatter_classes_are_not_reintroduced_anywhere():
    offenders = [
        f"{path}: {name}" for path in _python_sources()
        for name in sorted(_defined_names(_tree(path)) & _REMOVED_FORMATTER_CLASSES)
    ]
    assert offenders == []


def test_no_custom_logging_formatter_subclass_exists():
    offenders = [
        f"{path}: {node.name}" for path in _python_sources()
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.ClassDef) and any(_base_name(base) == "Formatter" for base in node.bases)
    ]
    assert offenders == []


def test_structlog_and_formatter_installation_only_in_logging_config():
    tree_by_path = {path: _tree(path) for path in SRC_ROOT.rglob("*.py")}
    assert [str(p) for p, t in tree_by_path.items() if p != LOGGING_PATH and _imports(t, "structlog")] == []
    assert [str(p) for p, t in tree_by_path.items() if p != LOGGING_PATH and "setFormatter" in _attribute_calls(t)] == []
    configure = next(
        node for node in tree_by_path[LOGGING_PATH].body
        if isinstance(node, ast.FunctionDef) and node.name == "configure_logging"
    )
    assert "ProcessorFormatter" in _attribute_calls(configure)


def test_redaction_functions_have_exactly_one_definition():
    for name in ("redact", "register_secret"):
        definitions = [
            str(path) for path in _python_sources()
            for node in ast.walk(_tree(path))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        ]
        assert definitions == [str(LOGGING_PATH)], f"{name} defined at {definitions}"
