"""Migration orchestration automation (Automation Phase A: discovery only).

This package reads the repository's existing migration control plane
(`docs/library-migration/STATUS.md`, `MASTER_PLAN.md`) and GitHub, and
reconciles them into a small machine-only state record. It is *not* a second
roadmap: `STATUS.md` and `MASTER_PLAN.md` remain the only source of truth for
what each phase contains and which phase runs next.

Phase A performs no repository mutation, no GitHub mutation, and no Claude or
OpenAI call. See `docs/library-migration/AUTOMATION.md`.
"""

from __future__ import annotations
