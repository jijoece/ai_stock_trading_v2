"""File-based fixture/result contract entry point.

docs/adr/0009-lumibot-backtest-distribution-boundary.md Decision 3: results
travel by file, never over stdin/stdout or the paper-runtime.v2 protocol.
The input and output documents are each validated independently against
`contract.py` -- this module does not trust the LumiBot-facing code in
`strategy.py` to have produced a well-formed result any more than it trusts
the caller-supplied input file.
"""
from __future__ import annotations

import json
import sys

from .contract import ContractError, parse_input_document, validate_result_document
from .strategy import BacktestExecutionError, run_backtest


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: python -m backtest_runtime <input_json_path> <output_json_path>",
            file=sys.stderr,
        )
        return 2
    input_path, output_path = argv

    try:
        with open(input_path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"backtest_runtime: could not read input: {exc}", file=sys.stderr)
        return 1

    try:
        backtest_input = parse_input_document(document)
    except ContractError as exc:
        print(f"backtest_runtime: invalid input: {exc}", file=sys.stderr)
        return 1

    try:
        result = run_backtest(backtest_input)
    except BacktestExecutionError as exc:
        print(f"backtest_runtime: backtest failed: {exc}", file=sys.stderr)
        return 1

    try:
        validate_result_document(result)
    except ContractError as exc:
        print(f"backtest_runtime: internal error, invalid result document: {exc}", file=sys.stderr)
        return 1

    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
        handle.write("\n")
    return 0
