"""Library-migration PR 18 review round 3 (P2): `pr18/AUDIT.md` and
`STATUS.md` claimed no code under `src/` changed, while the PR modifies
`src/trading_research/logging_config.py` (D16 ruling 5). The scope records
must name that source change and must not claim `src/` is untouched.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATION_DOCS = REPO_ROOT / "docs" / "library-migration"
AUDIT_PATH = MIGRATION_DOCS / "pr18" / "AUDIT.md"
STATUS_PATH = MIGRATION_DOCS / "STATUS.md"
DECISIONS_PATH = MIGRATION_DOCS / "DECISIONS.md"
CHANGED_SOURCE = "`src/trading_research/logging_config.py`"
_UNQUALIFIED_NO_SRC_CLAIM = re.compile(r"No code under `src/`")


def _audit_scope() -> str:
    """The audit's opening scope paragraph, before its first `##` section."""
    text = AUDIT_PATH.read_text()
    return text[: text.index("\n## ")]


def _status_header() -> str:
    """STATUS.md's current-phase header block, before the PR 17 paragraph."""
    text = STATUS_PATH.read_text()
    return text[: text.index("PR 17 — ")]


def _status_pr18_record() -> str:
    text = STATUS_PATH.read_text()
    start = text.index("## Completed work (PR 18)")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


@pytest.mark.parametrize(
    "section",
    [_audit_scope, _status_header, _status_pr18_record],
    ids=["audit-scope", "status-header", "status-pr18-record"],
)
def test_pr18_scope_record_names_the_logging_config_source_change(section):
    text = section()
    assert CHANGED_SOURCE in text
    assert "ruling 5" in text


@pytest.mark.parametrize(
    "section",
    [_audit_scope, _status_header],
    ids=["audit-scope", "status-header"],
)
def test_pr18_scope_record_does_not_claim_src_is_untouched(section):
    assert not _UNQUALIFIED_NO_SRC_CLAIM.search(section())


def test_d16_records_the_logging_redaction_ruling():
    text = DECISIONS_PATH.read_text()
    d16 = text[text.index("## D16 — ") :]
    assert "**Ruling 5: redact arbitrary log `extra` objects before serialization.**" in d16
