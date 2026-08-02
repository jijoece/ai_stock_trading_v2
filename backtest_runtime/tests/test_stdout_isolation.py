"""docs/adr/0009-lumibot-backtest-distribution-boundary.md, "stdout is not a
channel": LumiBot writes an unguarded startup banner and ANSI progress bars
directly to fd 1 the moment it is imported/run. The entry point redirects
`sys.stdout` to `sys.stderr` before importing `lumibot`, so this must hold
even if that redirect were somehow bypassed: results travel by file, so
whatever lands on the real stdout of the process cannot corrupt the result
file, which is written with its own `open()` call, never via `print`/
`sys.stdout`."""
from __future__ import annotations

import json
import subprocess
import sys

from backtest_runtime.contract import validate_result_document


def test_stdout_contamination_cannot_corrupt_the_result_file(tmp_path):
    from support.fixtures import valid_input_document

    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    input_path.write_text(json.dumps(valid_input_document()))

    import os

    env = {"PATH": os.environ.get("PATH", "")}
    completed = subprocess.run(
        [sys.executable, "-m", "backtest_runtime", str(input_path), str(output_path)],
        env=env,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr

    # The result file must be exactly one well-formed JSON document -- no
    # banner text, progress-bar escape codes, or log lines interleaved into
    # it, which would happen if stdout (not a dedicated file handle) were
    # ever the result channel.
    raw = output_path.read_text()
    document = json.loads(raw)
    validate_result_document(document)

    # And the banner/progress-bar text that LumiBot does write must have
    # gone to stderr, not to the process's real stdout -- proving the
    # redirect actually ran, not just that the file-based contract makes it
    # harmless.
    assert "LumiBot" not in completed.stdout
    assert "LumiBot" in completed.stderr
