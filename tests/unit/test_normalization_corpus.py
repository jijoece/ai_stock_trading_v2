"""PR 9 item 3: semantic drift protection beyond constant/helper-name
equality.

`test_runtime_normalization_contract.py` proves the two `normalization.py`
files declare the same vocabulary and the same function names by AST
comparison — it does not prove they make the same accept/reject *decisions*
for a given input, or produce the same canonical output. This module runs
`tests/fixtures/normalization_corpus.json` (one declarative corpus, shared
with `paper_runtime/tests/test_normalization_corpus.py`) against this
side's `normalization.py`; the runtime side's copy of this test runs the
identical corpus against its own module. Reading the same JSON file from
both test suites is not a shared Python import — ADR 0002/0009 forbid the
two distributions from importing each other, not from agreeing on test
data — so this does not create a shared installable package.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_research.runtime import normalization as norm

_CORPUS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "normalization_corpus.json"
_CASES = json.loads(_CORPUS_PATH.read_text())["cases"]


def _canonicalize(function_name: str, result: object) -> object:
    if function_name == "parse_decimal":
        return format(result, "f")
    return result


def _case_id(case: dict) -> str:
    return f"{case['function']}({case['args'][0]!r})"


@pytest.mark.parametrize("case", _CASES, ids=_case_id)
def test_corpus_case(case: dict) -> None:
    func = getattr(norm, case["function"])
    if case["accept"]:
        result = func(*case["args"])
        assert _canonicalize(case["function"], result) == case["canonical"]
    else:
        with pytest.raises(norm.NormalizationError):
            func(*case["args"])
