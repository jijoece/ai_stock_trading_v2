"""Proves docs/adr/0009-lumibot-backtest-distribution-boundary.md Decision 2's
five credential-safety properties against the REAL production entry point
(`backtest_runtime.__main__`), run as a subprocess so each scenario gets its
own controlled environment and working directory -- exactly the methodology
`docs/library-migration/pre-step-06/EVALUATION.md` section 2.3 used, now
permanently maintained here instead of as a docs/ evidence script.

No `importorskip`: every test in this module must fail, not skip, if
`lumibot` (a base dependency of this distribution) is missing.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SUPPORT_DIR = Path(__file__).resolve().parent / "support"
CREDENTIAL_PROBE = SUPPORT_DIR / "credential_probe.py"
UNPROTECTED_PROBE = SUPPORT_DIR / "unprotected_import_probe.py"

# Obviously-fake sentinel values. Never real credentials.
PROCENV_SENTINELS = {
    "ALPACA_API_KEY": "BACKTEST-RUNTIME-SENTINEL-PROCENV-2a7ed915-ALPACA-KEY",
    "ALPACA_API_SECRET": "BACKTEST-RUNTIME-SENTINEL-PROCENV-2a7ed915-ALPACA-SECRET",
    "ALPACA_IS_PAPER": "true",
}
DOTENV_SENTINEL_LINES = (
    'ALPACA_API_KEY=BACKTEST-RUNTIME-SENTINEL-DOTENV-9f1c7a3e-ALPACA-KEY\n'
    'ALPACA_API_SECRET=BACKTEST-RUNTIME-SENTINEL-DOTENV-9f1c7a3e-ALPACA-SECRET\n'
)


def _run_probe(tmp_path: Path, env: dict, cwd: Path | None = None) -> dict:
    diagnostics_path = tmp_path / "diagnostics.json"
    subprocess.run(
        [sys.executable, str(CREDENTIAL_PROBE), str(diagnostics_path)],
        env=env,
        cwd=str(cwd or tmp_path),
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return json.loads(diagnostics_path.read_text())


def _clean_env(**overrides) -> dict:
    # A minimal, explicit environment -- not `os.environ.copy()` -- so a
    # scenario only contains what it deliberately sets, never anything this
    # already-scrubbed pytest process happens to carry.
    import os

    env = {"PATH": os.environ.get("PATH", "")}
    env.update(overrides)
    return env


def test_inherited_process_credentials_are_scrubbed_before_import(tmp_path):
    env = _clean_env(**PROCENV_SENTINELS)
    diagnostics = _run_probe(tmp_path, env)
    assert diagnostics["credential_values_from_environment"] == []
    assert diagnostics["sentinel_env_keys_after_run"] == []
    assert diagnostics["broker_is_none"] is True
    assert diagnostics["data_source_is_none"] is True


def test_sentinel_dotenv_and_dotenv_local_are_not_loaded(tmp_path):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / ".env").write_text(DOTENV_SENTINEL_LINES)
    (workdir / ".env.local").write_text(DOTENV_SENTINEL_LINES)
    env = _clean_env()
    diagnostics = _run_probe(tmp_path, env, cwd=workdir)
    assert diagnostics["sentinel_env_keys_after_run"] == []
    assert diagnostics["credential_values_from_environment"] == []
    assert diagnostics["broker_is_none"] is True
    assert diagnostics["data_source_is_none"] is True


def test_exactly_the_documented_benign_defaults_resolve(tmp_path):
    # DATADOWNLOADER_API_KEY_HEADER is read lazily, during data-source setup
    # for an actual backtest run -- not merely on `import lumibot` -- so this
    # must run a real backtest (matching how the pre-step's own evidence,
    # docs/library-migration/pre-step-06/EVALUATION.md section 2.3, measured
    # it: runs S0/S2/S3/S4 all executed a full backtest, not just an import).
    from backtest_runtime.credential_guard import BENIGN_LUMIBOT_DEFAULT_CREDENTIAL_NAMES
    from support.fixtures import valid_input_document

    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    input_path.write_text(json.dumps(valid_input_document()))
    diagnostics_path = tmp_path / "diagnostics.json"
    subprocess.run(
        [
            sys.executable,
            str(CREDENTIAL_PROBE),
            str(diagnostics_path),
            str(input_path),
            str(output_path),
        ],
        env=_clean_env(),
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    diagnostics = json.loads(diagnostics_path.read_text())
    assert diagnostics["exit_code"] == 0
    assert set(diagnostics["credential_reads_with_values"]) == set(
        BENIGN_LUMIBOT_DEFAULT_CREDENTIAL_NAMES
    )
    assert diagnostics["credential_values_from_environment"] == []


def test_broker_and_live_data_source_remain_none_across_a_real_run(tmp_path):
    from support.fixtures import valid_input_document

    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    input_path.write_text(json.dumps(valid_input_document()))
    env = _clean_env(**PROCENV_SENTINELS)
    diagnostics_path = tmp_path / "diagnostics.json"
    subprocess.run(
        [
            sys.executable,
            str(CREDENTIAL_PROBE),
            str(diagnostics_path),
            str(input_path),
            str(output_path),
        ],
        env=env,
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    diagnostics = json.loads(diagnostics_path.read_text())
    assert diagnostics["exit_code"] == 0
    assert diagnostics["broker_is_none"] is True
    assert diagnostics["data_source_is_none"] is True
    assert diagnostics["credential_values_from_environment"] == []
    assert output_path.exists()


@pytest.mark.parametrize(
    "scenario",
    ["inherited_process_credential", "sentinel_dotenv_in_cwd"],
)
def test_without_the_guard_the_same_sentinel_would_leak(tmp_path, scenario):
    """Negative control: proves the guard is doing real work, not that there
    was never anything to protect against (mirrors
    docs/library-migration/pre-step-06/EVALUATION.md section 2.3's positive
    controls S1/S5/P1).

    The probe script (and its `guards` helper) are copied to a scratch
    location OUTSIDE this repository before being run: LumiBot's `.env`
    discovery walks upward from `sys.argv[0]`'s directory as well as the
    CWD, and this repository's own real (gitignored) `.env` sits a few
    directories above `tests/support/` -- running the script from inside the
    repo would let the script-directory walk find the real `.env` before
    the CWD walk ever reaches the sentinel one set up below, invalidating
    the control.
    """
    import os
    import shutil

    scratch = tmp_path / "outside_repo_scratch"
    scratch.mkdir()
    shutil.copy(UNPROTECTED_PROBE, scratch / "unprotected_import_probe.py")
    shutil.copy(SUPPORT_DIR / "guards.py", scratch / "guards.py")

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    env = {"PATH": os.environ.get("PATH", "")}
    if scenario == "inherited_process_credential":
        env.update(PROCENV_SENTINELS)
    else:
        (workdir / ".env").write_text(DOTENV_SENTINEL_LINES)

    diagnostics_path = tmp_path / "diagnostics.json"
    subprocess.run(
        [sys.executable, str(scratch / "unprotected_import_probe.py"), str(diagnostics_path)],
        env=env,
        cwd=str(workdir),
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    diagnostics = json.loads(diagnostics_path.read_text())
    assert diagnostics["broker_is_none"] is False, (
        "the unprotected probe was expected to leak the sentinel and construct a "
        "broker -- if it did not, the sentinel setup itself is broken and the "
        "protected-path tests above are not proving anything"
    )
    assert diagnostics["sentinel_env_keys_after_import"] != []
