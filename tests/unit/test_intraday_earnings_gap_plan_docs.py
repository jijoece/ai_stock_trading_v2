"""Review round 0 (P2) on PR 42: the intraday earnings-gap plan must not allow look-ahead.

1. `post_report_overnight` enters in the post-market, so its candidate filter may not use
   next-morning (pre-market / regular-session) or 20:00 observations.
2. Regular-session signals that need a completed bar's close must fill on the next bar,
   not at the signal bar's own close.
"""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PLAN = (REPO_ROOT / "docs" / "intraday-earnings-gap-plan.md").read_text()


def _section(start: str, end: str) -> str:
    begin = PLAN.index(start)
    return PLAN[begin : PLAN.index(end, begin)]


def _row(section: str, prefix: str) -> str:
    return next(line for line in section.splitlines() if line.startswith(prefix))


def test_candidate_filter_is_decided_per_variant_with_an_as_of_time():
    filters = _section("### 5.2 Candidate filter", "### 5.3 Entry variants")
    assert "per variant" in filters and "`as_of`" in filters
    assert "Every check below uses only data knowable before the variant's earliest entry" not in filters


def test_overnight_filter_excludes_next_morning_and_20_00_signals():
    filters = _section("### 5.2 Candidate filter", "### 5.3 Entry variants")
    overnight = _row(filters, "| `post_report_overnight`")
    assert "`post_report_move ≥ minimum_gap_percent`" in overnight
    must_not_use = overnight.split("|")[4]
    for banned in ("`post_hold`", "every pre-market signal", "reaction day"):
        assert banned in must_not_use
    for banned in ("premarket_gap", "premarket_hold", "premarket_rel_volume"):
        assert banned not in overnight.split("|")[3]


def test_premarket_entry_filter_is_as_of_the_decision_time_not_the_09_25_snapshot():
    filters = _section("### 5.2 Candidate filter", "### 5.3 Entry variants")
    row = _row(filters, "| `premarket_entry`")
    assert "recomputed as of that time" in row
    assert "including the 09:25 snapshot" in row


def test_step_3_requires_overnight_invariance_tests():
    step = _section("### Step 3", "### Step 4")
    assert "invariance tests" in step
    assert "`post_report_overnight` eligibility must not change" in step


def test_close_derived_signals_fill_at_the_next_bar_open():
    fill = _row(PLAN, "| D5 ")
    assert "**next** bar" in fill and "next bar's open plus slippage" in fill
    assert "never at the signal bar's close" in fill
    for signal in ("`orb`", "`pmh_break`", "`vwap_pullback`"):
        assert signal in fill
    assert "Enter at the signal bar's close plus slippage" not in PLAN


def test_step_4_requires_adverse_gap_tests_after_a_signal_close():
    step = _section("### Step 4", "### Step 5")
    assert "adverse-gap test" in step and "never the signal bar's close" in step
