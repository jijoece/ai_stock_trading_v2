"""Vectorized signal-matrix research adapter (library-migration PR 5).

Additive, evaluation-only capability built on VectorBT. This package has no
execution authority: it never places orders, never touches `paper_books`
accounting, and is not wired into any scheduled or live code path. See
`docs/library-migration/STATUS.md` and `DECISIONS.md` D4 for the license
decision and scope.
"""
from __future__ import annotations

from .adapter import ParameterSweepResult, VectorResearchInputError, run_parameter_sweep

__all__ = ["ParameterSweepResult", "VectorResearchInputError", "run_parameter_sweep"]
