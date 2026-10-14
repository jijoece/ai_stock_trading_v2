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
import sys

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


def _formatter_names(tree: ast.Module) -> set[str]:
    """Every local name bound to a formatter class: the base name itself,
    any `from ... import XFormatter as Alias`, and any `Alias = ...Formatter`
    assignment chain, so an alias cannot hide a formatter base."""
    names = {"Formatter"}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            bound: list[str] = []
            if isinstance(node, ast.ImportFrom):
                bound = [alias.asname for alias in node.names if alias.asname and _is_formatter_name(alias.name, names)]
            elif isinstance(node, ast.Assign) and _is_formatter_name(_base_name(node.value), names):
                bound = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and node.value and isinstance(node.target, ast.Name):
                if _is_formatter_name(_base_name(node.value), names):
                    bound = [node.target.id]
            for name in bound:
                if name not in names:
                    names.add(name)
                    changed = True
    return names


def _is_formatter_name(name: str, aliases: set[str]) -> bool:
    return name in aliases or name.endswith("Formatter")


def _formatter_subclass_offenders() -> list[str]:
    offenders: list[str] = []
    for path in _python_sources():
        tree = _tree(path)
        aliases = _formatter_names(tree)
        offenders.extend(
            f"{path}: {node.name}" for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and any(_is_formatter_name(_base_name(base), aliases) for base in node.bases)
        )
    return offenders


def test_no_custom_logging_formatter_subclass_exists():
    assert _formatter_subclass_offenders() == []


def test_formatter_subclass_guard_resolves_aliases(tmp_path, monkeypatch):
    """PR 18 review round 4 (P2, r4261095856): the guard compared only the
    syntactic base name against `Formatter`, so
    `from logging import Formatter as BaseFormatter` -- or any rebinding of
    a formatter class to an innocuous name -- produced a second formatter
    authority that passed every logging guard."""
    script = tmp_path / "rogue_formatter.py"
    script.write_text(
        "import logging as lg\n"
        "from logging import Formatter as BaseFormatter\n"
        "from structlog.stdlib import ProcessorFormatter as PF\n"
        "Base = BaseFormatter\n"
        "Renamed: type = Base\n"
        "class ImportAlias(BaseFormatter): ...\n"
        "class AssignAlias(Renamed): ...\n"
        "class ModuleAlias(lg.Formatter): ...\n"
        "class Processor(PF): ...\n"
        "class Unrelated(object): ...\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "SCRIPTS_ROOT", tmp_path)
    assert _formatter_subclass_offenders() == [
        f"{script}: {name}" for name in ("ImportAlias", "AssignAlias", "ModuleAlias", "Processor")
    ]


# Stdlib entry points that install a handler formatter without
# `setFormatter`: `basicConfig` always does (the default format included),
# and `dictConfig`/`fileConfig` can declare formatters.
_FORMATTER_INSTALLING_CALLS = ("basicConfig", "dictConfig", "fileConfig")


def _assigns_formatter_attribute(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Attribute) and node.attr == "formatter" and isinstance(node.ctx, ast.Store)
        for node in ast.walk(tree)
    )


def _formatter_authority_offenders() -> list[str]:
    tree_by_path = {path: _tree(path) for path in _python_sources() if path != LOGGING_PATH}
    offenders = []
    for path, tree in tree_by_path.items():
        called = _attribute_calls(tree) | _called_names(tree)
        found = (
            ("structlog", _imports(tree, "structlog")),
            ("setFormatter", "setFormatter" in called),
            (".formatter assignment", _assigns_formatter_attribute(tree)),
            *((name, name in called) for name in _FORMATTER_INSTALLING_CALLS),
        )
        offenders.extend(f"{path}: {kind}" for kind, hit in found if hit)
    return offenders


def _is_processor_formatter_call(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Call) and _base_name(node.func) == "ProcessorFormatter"


def _installed_formatter_offenders() -> list[str]:
    """Traces each `setFormatter(...)` argument in `configure_logging` back
    to its value: a `ProcessorFormatter(...)` call, or a name whose every
    binding in the function is one."""
    configure = next(
        node for node in _tree(LOGGING_PATH).body
        if isinstance(node, ast.FunctionDef) and node.name == "configure_logging"
    )
    # Only a plain `name = value` (or annotated/walrus) binding keeps its
    # value; any other store (augmented, unpacking, loop, with, del) is None.
    direct_values = {
        id(target): node.value for node in ast.walk(configure)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
    }
    bindings: dict[str, list[ast.AST | None]] = {}
    for node in ast.walk(configure):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bindings.setdefault(node.id, []).append(direct_values.get(id(node)))
    calls = [
        node for node in ast.walk(configure)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setFormatter"
    ]
    if not calls:
        return ["configure_logging installs no formatter"]
    offenders = []
    for call in calls:
        argument = call.args[0] if len(call.args) == 1 and not call.keywords else None
        if _is_processor_formatter_call(argument):
            continue
        values = bindings.get(argument.id, []) if isinstance(argument, ast.Name) else []
        if not values or not all(_is_processor_formatter_call(value) for value in values):
            offenders.append(f"line {call.lineno}: {ast.unparse(call)}")
    return offenders


def test_structlog_and_formatter_installation_only_in_logging_config():
    assert _formatter_authority_offenders() == []
    assert _installed_formatter_offenders() == []


def test_installed_formatter_guard_traces_the_set_formatter_argument(tmp_path, monkeypatch):
    """PR 18 review round 11 (P2, r4266942257): the guard checked only that
    some `ProcessorFormatter(...)` call existed in `configure_logging`, so
    rebinding `formatter = logging.Formatter()` before `setFormatter`
    while keeping the unused call passed every guard."""
    source = LOGGING_PATH.read_text()
    original = "    handler.setFormatter(formatter)\n"
    assert original in source
    mutated = tmp_path / "logging_config.py"
    monkeypatch.setattr(sys.modules[__name__], "LOGGING_PATH", mutated)
    for replacement in (
        "    formatter = logging.Formatter()\n" + original,
        "    handler.setFormatter(logging.Formatter())\n",
        "    handler.setFormatter(other)\n",
        "",
    ):
        mutated.write_text(source.replace(original, replacement))
        assert _installed_formatter_offenders() != [], replacement
    mutated.write_text(source.replace(original, "    handler.setFormatter(structlog.stdlib.ProcessorFormatter())\n"))
    assert _installed_formatter_offenders() == []


def test_formatter_authority_guard_scans_scripts(tmp_path, monkeypatch):
    """PR 18 review round 2 (P2): the guard scanned only `SRC_ROOT`, so a
    script importing structlog or installing a formatter passed despite
    D16's `scripts/` scope."""
    script = tmp_path / "rogue_logging.py"
    script.write_text("import logging\nimport structlog\nlogging.StreamHandler().setFormatter(None)\n")
    monkeypatch.setattr(sys.modules[__name__], "SCRIPTS_ROOT", tmp_path)
    assert _formatter_authority_offenders() == [f"{script}: structlog", f"{script}: setFormatter"]


def test_formatter_authority_guard_detects_alternate_installation_paths(tmp_path, monkeypatch):
    """PR 18 review round 12 (P2, r4267003364): the guard recognized only
    `setFormatter`, so assigning `handler.formatter` directly or calling
    `logging.basicConfig` (or `dictConfig`/`fileConfig`) installed a second
    formatter that no guard reported."""
    monkeypatch.setattr(sys.modules[__name__], "SCRIPTS_ROOT", tmp_path)
    cases = {
        "attribute.py": ("import logging\nlogging.StreamHandler().formatter = logging.Formatter()\n", ".formatter assignment"),
        "basic.py": ("import logging\nlogging.basicConfig(format='%(message)s')\n", "basicConfig"),
        "imported.py": ("from logging import basicConfig\nbasicConfig()\n", "basicConfig"),
        "dict.py": ("import logging.config\nlogging.config.dictConfig({})\n", "dictConfig"),
        "file.py": ("from logging.config import fileConfig\nfileConfig('x.ini')\n", "fileConfig"),
    }
    for name, (source, kind) in cases.items():
        (tmp_path / name).write_text(source)
    expected = sorted(f"{tmp_path / name}: {kind}" for name, (_, kind) in cases.items())
    assert sorted(_formatter_authority_offenders()) == expected


def test_redaction_functions_have_exactly_one_definition():
    for name in ("redact", "register_secret"):
        definitions = [
            str(path) for path in _python_sources()
            for node in ast.walk(_tree(path))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        ]
        assert definitions == [str(LOGGING_PATH)], f"{name} defined at {definitions}"
