"""Library-migration PR 18 (`docs/library-migration/DECISIONS.md` D16
Rulings 2 and 2a): `Approved, submit this exact paper order.` is an
operator-conversation gate that no code checks, so the runbook is its only
enforcement point. Each command that transmits an order to the broker must
have the phrase gate in the prose directly before its command block.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNBOOK_PATH = REPO_ROOT / "docs" / "runbooks" / "alpaca-paper-operations.md"
APPROVAL_PHRASE = "`Approved, submit this exact paper order.`"
_FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)


def _prose_before_command(text: str, command: str) -> str:
    """Returns the prose between the previous fenced block (or section
    heading, whichever is later) and the fenced block running `command`."""
    blocks = list(_FENCE.finditer(text))
    for index, block in enumerate(blocks):
        if re.search(rf"\bcli {re.escape(command)}\b", block.group(1)):
            start = blocks[index - 1].end() if index else 0
            heading = text.rfind("\n## ", start, block.start())
            return text[max(start, heading):block.start()]
    raise AssertionError(f"runbook has no command block for {command}")


@pytest.mark.parametrize("command", ["external-paper-submit", "external-paper-retry-submit"])
def test_broker_transmitting_command_is_preceded_by_the_approval_gate(command):
    assert APPROVAL_PHRASE in _prose_before_command(RUNBOOK_PATH.read_text(), command)


def test_retry_requires_fresh_approval_rather_than_the_original_submit_approval():
    """PR 18 review round 2 (P1): the runbook gated only the first submit,
    so an agent could run the retry on `NOT_FOUND` evidence and CLI
    arguments alone."""
    prose = " ".join(_prose_before_command(RUNBOOK_PATH.read_text(), "external-paper-retry-submit").split())
    assert "needs fresh approval" in prose
    assert "does not carry over" in prose
