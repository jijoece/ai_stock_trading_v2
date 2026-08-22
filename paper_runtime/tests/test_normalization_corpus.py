"""PR 9 item 3: the runtime side of the shared semantic drift corpus.

See `tests/unit/test_normalization_corpus.py` (main repository) for the
full rationale. This file runs the identical
`tests/fixtures/normalization_corpus.json` corpus against
`trading_paper_runtime.normalization` — proving this side accepts/rejects
every case identically to the main side and produces the same canonical
output, not merely that the two files declare the same constants/names.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_paper_runtime import normalization as norm

_CORPUS_PATH = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "normalization_corpus.json"
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
