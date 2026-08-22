"""The orchestrator's machine-only state record (`.agent/state.json`).

This file deliberately holds no roadmap content. `MASTER_PLAN.md`,
`STATUS.md`, `DECISIONS.md`, and the ADRs remain the substantive record; this
holds only what a fresh process cannot re-derive from GitHub — chiefly
`last_reviewed_sha` and `review_round`, which exist to stop a review being paid
for twice on one SHA and to stop an endless review/fix loop.

It is a cache, never an authority. `reconcile.py` recomputes every other field
from the migration documents and GitHub on each run.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any


# Orchestrator states. Phase A can *compute* every one of these; it acts on
# none of them.
DISCOVER = "DISCOVER"
WAITING_FOR_IMPLEMENTATION = "WAITING_FOR_IMPLEMENTATION"
IMPLEMENTING = "IMPLEMENTING"
WAITING_FOR_CI = "WAITING_FOR_CI"
WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
FIX_REQUIRED = "FIX_REQUIRED"
WAITING_FOR_CLAUDE_QUOTA = "WAITING_FOR_CLAUDE_QUOTA"
READY_TO_MERGE = "READY_TO_MERGE"
WAITING_FOR_MERGE = "WAITING_FOR_MERGE"
ADVANCE_PHASE = "ADVANCE_PHASE"
DONE = "DONE"
HUMAN_REQUIRED = "HUMAN_REQUIRED"

ALL_STATES = (
    DISCOVER,
    WAITING_FOR_IMPLEMENTATION,
    IMPLEMENTING,
    WAITING_FOR_CI,
    WAITING_FOR_REVIEW,
    FIX_REQUIRED,
    WAITING_FOR_CLAUDE_QUOTA,
    READY_TO_MERGE,
    WAITING_FOR_MERGE,
    ADVANCE_PHASE,
    DONE,
    HUMAN_REQUIRED,
)

# `next_action` values Phase A can propose. Each names work a later automation
# phase performs; Phase A only reports it.
ACTION_NONE = "none"
ACTION_IMPLEMENT = "implement"
ACTION_WAIT_FOR_CI = "wait_for_ci"
ACTION_FIX_CI = "fix_ci"
ACTION_REVIEW = "review"
ACTION_FIX_FINDINGS = "fix_findings"
ACTION_WAIT_FOR_HUMAN_MERGE = "wait_for_human_merge"
ACTION_MERGE = "merge"
ACTION_ADVANCE_PHASE = "advance_phase"
ACTION_ESCALATE = "escalate_to_human"


@dataclass(frozen=True)
class AutomationState:
    """Durable machine state, small enough to review in a diff."""

    active_phase: str | None = None
    github_pr: int | None = None
    branch: str | None = None
    head_sha: str | None = None
    last_reviewed_sha: str | None = None
    review_round: int = 0
    state: str = DISCOVER
    next_action: str = ACTION_NONE
    updated_at: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["notes"] = list(self.notes)
        return data

    def with_notes(self, notes: tuple[str, ...]) -> "AutomationState":
        return replace(self, notes=notes)


class StateError(ValueError):
    """Raised when `.agent/state.json` exists but cannot be understood."""


def state_from_dict(raw: dict[str, Any] | None) -> AutomationState:
    """Rebuild state from parsed JSON, tolerating an absent or empty record."""
    if not raw:
        return AutomationState()
    if not isinstance(raw, dict):
        raise StateError("state root must be a mapping")

    declared = raw.get("state") or DISCOVER
    if declared not in ALL_STATES:
        raise StateError(f"unknown state {declared!r}")
    round_value = raw.get("review_round", 0) or 0
    if not isinstance(round_value, int) or isinstance(round_value, bool) or round_value < 0:
        raise StateError(f"`review_round` must be a non-negative integer, got {round_value!r}")

    return AutomationState(
        active_phase=raw.get("active_phase"),
        github_pr=raw.get("github_pr"),
        branch=raw.get("branch"),
        head_sha=raw.get("head_sha"),
        last_reviewed_sha=raw.get("last_reviewed_sha"),
        review_round=round_value,
        state=declared,
        next_action=raw.get("next_action") or ACTION_NONE,
        updated_at=raw.get("updated_at"),
        notes=tuple(raw.get("notes") or ()),
    )


def load_state(path: Path) -> AutomationState:
    """Load state from `path`; a missing file is a valid cold start."""
    if not path.exists():
        return AutomationState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "null")
    except json.JSONDecodeError as error:
        raise StateError(f"{path} is not valid JSON: {error}") from error
    return state_from_dict(raw)


def save_state(path: Path, state: AutomationState) -> None:
    """Write state atomically so an interrupted run cannot truncate it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)
