"""Re-launch a script under the repository's `.venv` when the current Python lacks the project.

Standard library only, so it can run under any `python3`. The scripts that need numpy, TA-Lib or the
`trading_research` package call `reexec_in_project_venv()` when run directly and an import fails;
`python3 scripts/score.py ...` then behaves like `.venv/bin/python scripts/score.py ...`.

It never installs anything and never searches outside the repository: it only looks for
`<repo>/.venv/bin/python`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

GUARD_ENV = "TRADING_RESEARCH_VENV_REEXEC"
_REPO = Path(__file__).resolve().parent.parent


def project_venv_python() -> Optional[Path]:
    """The repo's venv interpreter, or None when there is no usable one."""
    for rel in ("bin/python", "Scripts/python.exe"):
        candidate = _REPO / ".venv" / rel
        if candidate.exists():
            return candidate
    return None


def _already_in_venv(venv_python: Path) -> bool:
    return Path(sys.prefix).resolve() == venv_python.parent.parent.resolve()


def reexec_in_project_venv(script: str) -> bool:
    """Replace this process with `<repo>/.venv` python running `script` and the same arguments.

    Returns False, leaving the process untouched, when re-launching cannot help: no venv exists, this
    already is the venv, or a re-launch already happened (loop guard). It does not return on success.
    """
    venv_python = project_venv_python()
    if venv_python is None or os.environ.get(GUARD_ENV) or _already_in_venv(venv_python):
        return False
    os.environ[GUARD_ENV] = "1"
    os.execv(str(venv_python), [str(venv_python), str(Path(script).resolve()), *sys.argv[1:]])
    return True  # unreachable; keeps type checkers satisfied


def missing_environment_message(what: str) -> str:
    return (f"{what} is not available in this Python and no project .venv was found. Create one with "
            "`python3 -m venv .venv && .venv/bin/python -m pip install -e '.[dev,indicators]'`, "
            "then run this script again.")
