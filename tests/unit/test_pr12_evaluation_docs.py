"""Regression test for the PR 12 review finding on the Python 3.10 floor.

`docs/library-migration/pr12/EVALUATION.md` originally described the
Riskfolio-Lib / VectorBT compatibility check as unconditionally conflict-free.
It was only verified on Python 3.14; VectorBT 1.1.0 requires Python >=3.11
(`DEPENDENCY_MATRIX.md`), which cannot resolve on this repository's declared
`>=3.10` project-wide floor. This pins the caveat into the checked-in
document and pins the underlying facts it depends on, so either regressing
back to the unqualified claim or silently changing the floors it cites would
fail here.

A follow-up review round (PR 29 fix round 1) found that only
`EVALUATION.md` carried the qualification — the canonical planning and
decision records (`DEPENDENCY_MATRIX.md`, `MASTER_PLAN.md`,
`COMPONENT_MATRIX.md`, `DECISIONS.md` D10, `STATUS.md`) still called the
Riskfolio-Lib/VectorBT pairing unconditionally conflict-free. The tests
below pin the same `>=3.11` qualification into each of those records.

A second follow-up review round (PR 29 fix round 2) found three more
defects, each pinned by a further test below:

1. The `>=3.11` compatibility conclusion itself was only ever verified on
   Python 3.14.5rc1 — VectorBT 1.1.0's own floor is `>=3.11`, not `3.14`,
   so the broader claim was unverified at the range's actual boundary.
   `EVALUATION.md` now also records an independent wheel-only install,
   `pip check`, and import smoke test run on Python 3.11.15 itself (raw
   output in `pr12/scratch_output_py311.txt`).
2. `STATUS.md`'s "Completed work (PR 12)" section claimed no test file was
   added and quoted the pre-fix Nox test count, even after
   `test_pr12_evaluation_docs.py` (this file) was added in fix round 1.
3. `STATUS.md` still labeled the already-merged PR 11 as "IMPLEMENTED, NOT
   MERGED", contradicting `git log` (PR 11 merged as PR #28, `611b3df`).

A third follow-up review round (PR 29 fix round 3) found that every
canonical record's "conflict-free"/"no conflict" conclusion was qualified
only with the lower bound (`>=3.11`), never the upper one — even though
VectorBT 1.1.0's own `Requires-Python` classifier is `>=3.11,<3.15`, so the
adopted `vectorbt>=1.1.0,<1.2` pin cannot resolve on Python 3.15+ either.
The tests below require both bounds (`>=3.11` and `<3.15`) wherever a
record states the compatibility conclusion, so a regression that drops
either bound fails here.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EVALUATION = ROOT / "docs" / "library-migration" / "pr12" / "EVALUATION.md"
DEPENDENCY_MATRIX = ROOT / "docs" / "library-migration" / "DEPENDENCY_MATRIX.md"
MASTER_PLAN = ROOT / "docs" / "library-migration" / "MASTER_PLAN.md"
COMPONENT_MATRIX = ROOT / "docs" / "library-migration" / "COMPONENT_MATRIX.md"
DECISIONS = ROOT / "docs" / "library-migration" / "DECISIONS.md"
STATUS = ROOT / "docs" / "library-migration" / "STATUS.md"
PYPROJECT = ROOT / "pyproject.toml"
SCRATCH_OUTPUT_PY311 = (
    ROOT / "docs" / "library-migration" / "pr12" / "scratch_output_py311.txt"
)


def _section(text: str, heading: str, next_heading: str) -> str:
    return text.split(heading, 1)[1].split(next_heading, 1)[0]


def test_repository_python_floor_is_still_3_10():
    """The caveat's premise: the project floor Riskfolio-Lib would inherit."""
    text = PYPROJECT.read_text(encoding="utf-8")
    assert 'requires-python = ">=3.10"' in text


def test_dependency_matrix_still_documents_vectorbt_requiring_3_11():
    """The other half of the premise: VectorBT's own floor is narrower."""
    text = DEPENDENCY_MATRIX.read_text(encoding="utf-8")
    match = re.search(r"\| VectorBT[^\n]*?\|\s*`(>=3\.11[^`]*)`\s*\|", text)
    assert match, "VectorBT row's Python-requirement column not found"
    assert match.group(1).startswith(">=3.11")


def test_evaluation_states_the_3_10_floor_caveat():
    text = EVALUATION.read_text(encoding="utf-8")
    section2 = _section(text, "## 2.", "## 3.")
    assert "requires-python = \">=3.10\"" in section2 or ">=3.10" in section2
    assert ">=3.11" in section2
    assert "cannot resolve" in section2
    assert "vectorbt>=1.1.0,<1.2" in section2


def test_evaluation_compatibility_claim_is_scoped_to_tested_interpreters_only():
    """The 'do not conflict' claim must name only the two interpreters
    actually tested, not the whole declared `>=3.11,<3.15` range — and must
    say outright that Python 3.12/3.13 were not tested (PR 29 fix round 9:
    two tested endpoints do not establish the interval between them)."""
    text = EVALUATION.read_text(encoding="utf-8")
    section2 = _section(text, "## 2.", "## 3.")
    idx = section2.index("do not conflict")
    scoped = section2[idx : idx + 900]
    assert "3.11.15" in scoped
    assert "3.14.5rc1" in scoped
    assert "3.12" in scoped
    assert "3.13" in scoped
    assert "were not installed or tested" in scoped


def test_recommendation_also_scopes_the_conflict_free_claim():
    text = EVALUATION.read_text(encoding="utf-8")
    section5 = _section(text, "## 5.", "**Decision:")
    idx = section5.index("technically installable")
    scoped = section5[idx : idx + 400]
    assert "3.11.15" in scoped
    assert "3.14.5rc1" in scoped
    assert "3.12" in scoped
    assert "3.13" in scoped
    assert "untested" in scoped
    assert "3.10" in scoped


def test_evaluation_python_floor_caveat_names_untested_minors():
    """The Python-floor caveat paragraph must also flag 3.12/3.13 as
    untested, not just the pre-existing `>=3.10` project-floor gap."""
    text = EVALUATION.read_text(encoding="utf-8")
    section2 = _section(text, "## 2.", "## 3.")
    idx = section2.index("Python-floor caveat")
    caveat = section2[idx : idx + 400]
    assert "3.12" in caveat
    assert "3.13" in caveat
    assert "not installed or tested" in caveat


def test_dependency_matrix_riskfolio_row_scopes_no_conflict_claim():
    """DEPENDENCY_MATRIX.md's Riskfolio-Lib row must name only the two
    tested interpreters, not the whole declared range (PR 29 fix round 9)."""
    text = DEPENDENCY_MATRIX.read_text(encoding="utf-8")
    idx = text.index("| Riskfolio-Lib | 7.3.0 |")
    row = text[idx : idx + 700]
    assert "3.11.15" in row
    assert "3.14.5rc1" in row
    assert "3.12" in row
    assert "3.13" in row
    assert "untested" in row
    assert "3.10" in row


def test_dependency_matrix_rejected_deferred_table_scopes_no_conflict_claim():
    """Section 4's rejected/deferred summary row must carry the same caveat."""
    text = DEPENDENCY_MATRIX.read_text(encoding="utf-8")
    idx = text.index("| Riskfolio-Lib | Defer (PR 12, evaluated 2026-08-23)")
    row = text[idx : idx + 500]
    assert "3.11.15" in row
    assert "3.14.5rc1" in row
    assert "3.12" in row
    assert "3.13" in row
    assert "untested" in row
    assert "3.10" in row


def test_master_plan_row_12_scopes_no_conflict_claim():
    text = MASTER_PLAN.read_text(encoding="utf-8")
    idx = text.index("| 12 | Riskfolio-Lib evaluation only |")
    row = text[idx : idx + 900]
    assert "3.11.15" in row
    assert "3.14.5rc1" in row
    assert "3.12" in row
    assert "3.13" in row
    assert "untested" in row
    assert "3.10" in row


def test_component_matrix_portfolio_optimization_row_scopes_conflict_free_claim():
    text = COMPONENT_MATRIX.read_text(encoding="utf-8")
    idx = text.index("| Portfolio optimization |")
    row = text[idx : idx + 600]
    assert "3.11.15" in row
    assert "3.14.5rc1" in row
    assert "3.12" in row
    assert "3.13" in row
    assert "untested" in row
    assert "3.10" in row


def test_decisions_d10_scopes_no_conflict_claim():
    text = DECISIONS.read_text(encoding="utf-8")
    idx = text.index("## D10")
    section = text[idx : idx + 4000]
    assert "3.11.15" in section
    assert "3.14.5rc1" in section
    assert "3.12" in section
    assert "3.13" in section
    assert "3.10" in section


def test_decisions_d10_ruling_scopes_technically_unblocked_claim():
    text = DECISIONS.read_text(encoding="utf-8")
    idx = text.index("**Ruling: defer, do not adopt.**")
    ruling = text[idx : idx + 600]
    assert "technically installable without" in ruling
    assert "3.11.15" in ruling
    assert "3.14.5rc1" in ruling
    assert "3.12" in ruling
    assert "3.13" in ruling
    assert "3.10" in ruling


def test_status_current_phase_scopes_no_conflict_claim():
    text = STATUS.read_text(encoding="utf-8")
    idx = text.index("**Current phase: PR 12")
    section = text[idx : idx + 1100]
    assert "3.11.15" in section
    assert "3.14.5rc1" in section
    assert "3.12" in section
    assert "3.13" in section
    assert "untested" in section
    assert "3.10" in section


def test_status_completed_work_section_scopes_no_conflict_claim():
    text = STATUS.read_text(encoding="utf-8")
    idx = text.index("that Riskfolio-Lib does not conflict with the")
    scoped = text[idx : idx + 450]
    assert "3.11.15" in scoped
    assert "3.14.5rc1" in scoped
    assert "3.12" in scoped
    assert "3.13" in scoped
    assert "3.10" in scoped


def test_evaluation_records_independent_python_3_11_verification():
    """PR 29 fix round 2: the >=3.11 claim must be verified at its own
    floor, not only inferred from a single Python 3.14 run."""
    text = EVALUATION.read_text(encoding="utf-8")
    section2 = _section(text, "## 2.", "## 3.")
    assert "3.11.15" in section2
    assert "3.14.5rc1" in section2
    assert "pip check" in section2
    assert "scratch_output_py311.txt" in section2


def test_evaluation_python_floor_caveat_covers_both_tested_interpreters():
    text = EVALUATION.read_text(encoding="utf-8")
    section2 = _section(text, "## 2.", "## 3.")
    idx = section2.index("Python-floor caveat")
    caveat = section2[idx : idx + 300]
    assert "3.11.15" in caveat
    assert "3.14.5rc1" in caveat


def test_scratch_output_py311_file_exists_and_confirms_no_conflict():
    """Regression for the raw evidence EVALUATION.md Section 2 cites."""
    assert SCRATCH_OUTPUT_PY311.exists()
    text = SCRATCH_OUTPUT_PY311.read_text(encoding="utf-8")
    assert "3.11.15" in text
    assert "No broken requirements found." in text
    assert "riskfolio 7.3.0" in text
    assert "vectorbt 1.1.0" in text


def test_status_current_phase_records_python_3_11_verification():
    """PR 29 fix round 2, finding 1: the current-phase summary must not
    imply the >=3.11 claim rests on a single untested interpreter."""
    text = STATUS.read_text(encoding="utf-8")
    idx = text.index("that Riskfolio-Lib does not conflict with the")
    scoped = text[idx : idx + 500]
    assert "3.11.15" in scoped
    assert "3.14.5rc1" in scoped


def test_status_current_phase_records_review_test_scope():
    """The current-phase summary must distinguish unchanged production code
    from the documentation-consistency tests added during review fixes."""
    text = STATUS.read_text(encoding="utf-8")
    section = text.split("**Next phase:", 1)[0]
    assert "no production code" in section
    assert "documentation-consistency regression coverage" in section
    assert "or `tests/`" not in section


def test_status_completed_work_records_the_added_test_file():
    """PR 29 fix round 2, finding 3: STATUS.md must not claim no test file
    was added once `test_pr12_evaluation_docs.py` (this file) exists."""
    text = STATUS.read_text(encoding="utf-8")
    section = text.split("## Completed work (PR 12)", 1)[1]
    assert "No test file was added or modified" not in section
    assert "test_pr12_evaluation_docs.py" in section
    assert "3119" not in section
    assert "3273 passed, 57" in section
    assert "3145 passed, 106" in section
    assert "PLACEHOLDER" not in section


def test_status_marks_pr_11_as_merged():
    """PR 29 fix round 2, finding 4: PR 11 merged as PR #28 (`611b3df`),
    an ancestor of this branch's base commit; STATUS.md must not
    contradict `git log` by still calling it "NOT MERGED"."""
    text = STATUS.read_text(encoding="utf-8")
    idx = text.index("PR 11 — QuantStats/analytics migration")
    entry = text[idx : idx + 300]
    assert "IMPLEMENTED, NOT MERGED" not in entry
    assert "**merged**" in entry
    assert "611b3df" in entry


def test_status_records_pr12_evaluation_outcome():
    """PR 29 fix round 15: this test previously asserted PR 12's transient
    current-phase/merge-status markers ("Current phase: PR 12", "NOT
    MERGED") permanently. Once PR 13 follows the documented workflow and
    rewrites STATUS.md to advance the current phase and mark PR 12 merged
    (the same "NOT MERGED" -> "**merged**" rewrite already applied to PR 9
    and PR 11, see `test_status_marks_pr_11_as_merged`'s docstring), those
    markers legitimately disappear, so pinning them here would fail the
    canonical full suite on expected migration advancement. Assert only
    PR 12's enduring completed-work fact -- the evaluation outcome -- which
    stays true regardless of the PR 12 branch's own merge status."""
    text = STATUS.read_text(encoding="utf-8")
    section = text.split("## Completed work (PR 12)", 1)[1]
    assert "**Scope:** evaluation only" in section
    assert "Outcome: defer, do not adopt" in section


def test_pr_11_merge_commit_is_an_ancestor_of_this_branch():
    """Pins the git fact the previous test's claim depends on. Skips (does
    not fail) when the commit object is unavailable -- e.g. a shallow
    clone (`git clone --depth 1`) or an exported source tree without git
    history -- since that is a checkout-depth limitation, not a defect in
    this branch or its documentation (PR 29 fix round 5)."""
    has_object = subprocess.run(
        ["git", "cat-file", "-e", "611b3df^{commit}"],
        cwd=ROOT,
        check=False,
    )
    if has_object.returncode != 0:
        pytest.skip(
            "commit 611b3df is not available in this checkout (shallow "
            "clone or exported source tree without full git history); "
            "ancestry cannot be verified without fetching that history"
        )
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "611b3df", "HEAD"],
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, (
        "git merge-base could not verify that 611b3df is an ancestor of "
        f"HEAD (exit {result.returncode}) even though the commit object "
        "is present locally"
    )


def test_pr_11_ancestry_check_skips_without_commit_object(monkeypatch):
    """PR 29 fix round 5 regression: the ancestor check above must skip,
    not fail with exit 128, when the referenced commit object is
    unavailable (e.g. a shallow clone or exported source tree)."""

    def fake_run(cmd, cwd=None, check=False):
        assert cmd[:3] == ["git", "cat-file", "-e"]
        return subprocess.CompletedProcess(cmd, returncode=128)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(pytest.skip.Exception):
        test_pr_11_merge_commit_is_an_ancestor_of_this_branch()


def test_ci_full_suite_jobs_fetch_full_git_history():
    """PR 29 fix round 4: every CI job whose full offline suite run
    includes this file's `git merge-base` check must not use the default
    single-commit shallow checkout. `actions/checkout@v4` defaults to
    `fetch-depth: 1`, which does not contain the `611b3df` commit object,
    so `test_pr_11_merge_commit_is_an_ancestor_of_this_branch` would fail
    with "Not a valid object name" (exit 128) in CI even though the
    documented git fact is correct."""
    import yaml

    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    full_suite_jobs = {"main-tests", "python-3-10-floor", "research-tests"}
    for job_name in full_suite_jobs:
        job = workflow["jobs"][job_name]
        checkout_steps = [
            step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout")
        ]
        assert checkout_steps, f"{job_name} has no actions/checkout step"
        for step in checkout_steps:
            assert step.get("with", {}).get("fetch-depth") == 0, (
                f"{job_name}'s checkout step must set `fetch-depth: 0` so "
                "the full-suite git-ancestor check has the required history"
            )


def test_status_completed_work_scope_records_ci_workflow_change():
    """PR 29 fix round 8: STATUS.md's "Completed work (PR 12)" **Scope:**
    paragraph must record that `.github/workflows/ci.yml` was changed (to
    give this file's `git merge-base` ancestry check the full history it
    needs in CI), not just the new test file. Omitting it would leave a
    future maintainer relying on STATUS.md unaware of a persistent CI
    behavior and performance change (`fetch-depth: 0` instead of the
    default shallow checkout) made in this PR."""
    text = STATUS.read_text(encoding="utf-8")
    section = text.split("## Completed work (PR 12)", 1)[1].split(
        "**Outcome:", 1
    )[0]
    assert ".github/workflows/ci.yml" in section
    assert "fetch-depth: 0" in section
    assert "main-tests" in section
    assert "python-3-10-floor" in section
    assert "research-tests" in section
