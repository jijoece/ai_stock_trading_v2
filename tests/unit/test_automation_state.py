"""The durable state record.

`.agent/state.json` exists so a fresh process — after a quota pause, a runner
restart, or a scheduled recovery run — can continue without the original Claude
conversation. It holds no roadmap content.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.automation import state as state_module
from scripts.automation.state import (
    AutomationState,
    StateError,
    load_state,
    save_state,
    state_from_dict,
)


def test_a_missing_state_file_is_a_valid_cold_start(tmp_path: Path) -> None:
    loaded = load_state(tmp_path / "state.json")
    assert loaded == AutomationState()
    assert loaded.state == state_module.DISCOVER
    assert loaded.review_round == 0


def test_state_survives_a_write_and_read_cycle(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "state.json"
    original = AutomationState(
        active_phase="9",
        github_pr=22,
        branch="migration/09-lumibot-normalization-contract",
        head_sha="3193b0b",
        last_reviewed_sha="3193b0b",
        review_round=2,
        state=state_module.WAITING_FOR_CLAUDE_QUOTA,
        next_action=state_module.ACTION_FIX_FINDINGS,
        updated_at="2026-08-22T00:00:00+00:00",
        notes=("quota exhausted mid-fix",),
    )
    save_state(path, original)
    assert load_state(path) == original


def test_a_quota_pause_preserves_everything_needed_to_resume(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(
        path,
        AutomationState(
            active_phase="9",
            github_pr=22,
            branch="migration/09-lumibot-normalization-contract",
            head_sha="3193b0b",
            last_reviewed_sha="a1b2c3d",
            review_round=2,
            state=state_module.WAITING_FOR_CLAUDE_QUOTA,
            next_action=state_module.ACTION_FIX_FINDINGS,
        ),
    )
    resumed = load_state(path)
    for field_name in (
        "active_phase",
        "github_pr",
        "branch",
        "head_sha",
        "last_reviewed_sha",
        "review_round",
        "state",
        "next_action",
    ):
        assert getattr(resumed, field_name) is not None


def test_the_state_file_is_readable_json(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(path, AutomationState(active_phase="9", github_pr=22))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["active_phase"] == "9"
    assert payload["github_pr"] == 22


def test_an_unknown_state_name_is_rejected_rather_than_assumed() -> None:
    with pytest.raises(StateError, match="unknown state"):
        state_from_dict({"state": "PROBABLY_FINE"})


def test_a_negative_review_round_is_rejected() -> None:
    with pytest.raises(StateError, match="review_round"):
        state_from_dict({"review_round": -1})


def test_corrupt_state_is_reported_not_silently_reset(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateError):
        load_state(path)


def test_an_interrupted_write_cannot_truncate_existing_state(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(path, AutomationState(active_phase="9", github_pr=22))
    assert not list(tmp_path.glob("*.tmp")), "the temporary file must not be left behind"
    assert load_state(path).active_phase == "9"
