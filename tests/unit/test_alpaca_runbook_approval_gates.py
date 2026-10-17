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


def test_retry_preview_refresh_is_documented_before_the_retry_and_needs_the_override():
    """PR 18 review round 3 (P2, r4255587023): under the override-only
    procedure, the read-only refresh is the documented way past an expired
    preview, and it must be listed among the commands that take the
    override file."""
    text = RUNBOOK_PATH.read_text()
    assert text.index("cli external-paper-refresh-retry-preview") < text.index("cli external-paper-retry-submit")
    enable_section = " ".join(text.split("## Enable deliberately", 1)[1].split("\n## ", 1)[0].split())
    assert "retry-preview refresh" in enable_section


def test_retry_requires_fresh_approval_rather_than_the_original_submit_approval():
    """PR 18 review round 2 (P1): the runbook gated only the first submit,
    so an agent could run the retry on `NOT_FOUND` evidence and CLI
    arguments alone."""
    prose = " ".join(_prose_before_command(RUNBOOK_PATH.read_text(), "external-paper-retry-submit").split())
    assert "needs fresh approval" in prose
    assert "does not carry over" in prose


def test_cancellation_requires_an_explicit_operator_request():
    """PR 18 review round 11 (P2, r4266942250, D16 Ruling 2b): the runbook
    supplied `external-paper-cancel`, which changes broker order state, with
    no approval step, and its rollback section said to "explicitly cancel"
    orders without naming whose decision that was."""
    text = RUNBOOK_PATH.read_text()
    prose = " ".join(_prose_before_command(text, "external-paper-cancel").split())
    assert "operator's own explicit request to cancel that client order ID" in prose
    assert "Do not cancel on your own initiative" in prose
    assert "does not cover a cancellation" in prose
    rollback = " ".join(text.split("## Rollback", 1)[1].split("\n## ", 1)[0].split())
    assert "only when the operator explicitly requests it" in rollback


def _accepts_paper_books_config(command: str, capsys) -> bool:
    from trading_research.cli import main

    with pytest.raises(SystemExit):
        main([command, "--help"])
    return "--paper-books-config" in capsys.readouterr().out


def test_every_documented_command_that_accepts_the_override_supplies_it(capsys):
    """PR 18 review round 6 (P2): the retry-preview refresh example omitted
    `--paper-books-config`, so following it loaded the tracked config, which
    has external paper disabled, and the refresh failed with
    `PAPER_BOOKS_DISABLED`. The flag set comes from the real CLI parser."""
    text = RUNBOOK_PATH.read_text()
    invocations = [
        " ".join(invocation.replace("\\\n", " ").split())
        for block in _FENCE.finditer(text)
        for invocation in re.split(r"\n(?=python -m trading_research\.cli )", block.group(1))
        if invocation.startswith("python -m trading_research.cli external-paper-")
    ]
    checked = set()
    for invocation in invocations:
        command = invocation.split()[3]
        if _accepts_paper_books_config(command, capsys):
            checked.add(command)
            assert "--paper-books-config <path>" in invocation, invocation
    assert "external-paper-refresh-retry-preview" in checked
    assert "external-paper-retry-submit" in checked
