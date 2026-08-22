"""Automation configuration loaded from `.agent/config.yaml`.

Every value has a safe default, and the committed default for `enabled` is
`False`: merging the automation infrastructure must never start the automation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_RELATIVE_PATH = Path(".agent/config.yaml")
DEFAULT_STATE_RELATIVE_PATH = Path(".agent/state.json")

# Branch naming already used by every migration PR in this repository
# (`migration/06-...`, `migration/08-...`, `migration/09-...`).
DEFAULT_BRANCH_PREFIX = "migration/"


class ConfigurationError(ValueError):
    """Raised when `.agent/config.yaml` is present but unusable."""


@dataclass(frozen=True)
class ClaudeConfig:
    implementation_model: str = "sonnet"
    escalation_model: str = "opus"
    quota_retry_hours: int = 3
    max_attempts_per_run: int = 1


@dataclass(frozen=True)
class ReviewConfig:
    provider: str = "codex"
    max_rounds: int = 3
    max_reviews_per_sha: int = 1
    blocking_severity: str = "P2"


@dataclass(frozen=True)
class PlannerConfig:
    provider: str = "openai"
    max_calls_per_phase: int = 2


@dataclass(frozen=True)
class MergeConfig:
    automatic: bool = False


@dataclass(frozen=True)
class AutomationConfig:
    """The whole automation configuration surface."""

    enabled: bool = False
    branch_prefix: str = DEFAULT_BRANCH_PREFIX
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    planner: PlannerConfig = field(default_factory=PlannerConfig)
    merge: MergeConfig = field(default_factory=MergeConfig)


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigurationError(f"`{name}` must be a mapping, got {type(value).__name__}")
    return value


def _typed(section: dict[str, Any], key: str, default: Any) -> Any:
    if key not in section or section[key] is None:
        return default
    value = section[key]
    # bool is a subclass of int; check it first so `enabled: 1` is rejected.
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ConfigurationError(f"`{key}` must be a boolean, got {value!r}")
        return value
    if isinstance(default, int) and not isinstance(value, bool):
        if not isinstance(value, int):
            raise ConfigurationError(f"`{key}` must be an integer, got {value!r}")
        if value < 0:
            raise ConfigurationError(f"`{key}` must not be negative, got {value!r}")
        return value
    if isinstance(default, str):
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"`{key}` must be a non-empty string, got {value!r}")
        return value
    raise ConfigurationError(f"unsupported configuration key `{key}`")


def parse_config(raw: dict[str, Any] | None) -> AutomationConfig:
    """Build a configuration from already-parsed YAML data."""
    if raw is None:
        return AutomationConfig()
    if not isinstance(raw, dict):
        raise ConfigurationError("configuration root must be a mapping")

    claude = _section(raw, "claude")
    review = _section(raw, "review")
    planner = _section(raw, "planner")
    merge = _section(raw, "merge")

    return AutomationConfig(
        enabled=_typed(raw, "enabled", False),
        branch_prefix=_typed(raw, "branch_prefix", DEFAULT_BRANCH_PREFIX),
        claude=ClaudeConfig(
            implementation_model=_typed(claude, "implementation_model", "sonnet"),
            escalation_model=_typed(claude, "escalation_model", "opus"),
            quota_retry_hours=_typed(claude, "quota_retry_hours", 3),
            max_attempts_per_run=_typed(claude, "max_attempts_per_run", 1),
        ),
        review=ReviewConfig(
            provider=_typed(review, "provider", "codex"),
            max_rounds=_typed(review, "max_rounds", 3),
            max_reviews_per_sha=_typed(review, "max_reviews_per_sha", 1),
            blocking_severity=_typed(review, "blocking_severity", "P2"),
        ),
        planner=PlannerConfig(
            provider=_typed(planner, "provider", "openai"),
            max_calls_per_phase=_typed(planner, "max_calls_per_phase", 2),
        ),
        merge=MergeConfig(automatic=_typed(merge, "automatic", False)),
    )


def load_config(path: Path) -> AutomationConfig:
    """Load configuration from `path`, falling back to defaults when absent."""
    if not path.exists():
        return AutomationConfig()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:  # pragma: no cover - message passthrough
        raise ConfigurationError(f"{path} is not valid YAML: {error}") from error
    return parse_config(raw)
