#!/usr/bin/env bash
# Reproduces every artifact under docs/library-migration/pr7/results/.
#
# Usage:
#   docs/library-migration/pr7/run_parity.sh \
#       <backtest_runtime_venv_python> [<main_project_python>]
#
# The two interpreters must come from two SEPARATE environments and are never
# combined (docs/adr/0009-lumibot-backtest-distribution-boundary.md Decision 1
# and Decision 5): the first is a venv where only `pip install -e
# backtest_runtime/` has been run, the second is the main project's `.venv`.
# The comparator reads the two result documents; nothing imports both engines.
#
# Example:
#   python3.11 -m venv /tmp/bt-venv
#   /tmp/bt-venv/bin/python -m pip install -e backtest_runtime/[dev]
#   docs/library-migration/pr7/run_parity.sh /tmp/bt-venv/bin/python .venv/bin/python
set -euo pipefail

BT_PYTHON="${1:?usage: run_parity.sh <backtest_runtime_venv_python> [<main_python>]}"
MAIN_PYTHON="${2:-.venv/bin/python}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../../.." && pwd)"
RESULTS="${HERE}/results"

# Both interpreters are resolved to absolute paths HERE, before anything below
# changes directory. The documented invocation passes them relative to the
# repository root (`.venv/bin/python`), and step 2 runs the isolated
# interpreter from a scratch directory -- a relative path would resolve
# against that scratch directory and fail.
absolute_interpreter() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        */*) printf '%s\n' "${PWD}/$1" ;;
        # A bare name is a PATH lookup, which `env -i PATH=...` preserves,
        # but pin it now so both steps use the same interpreter.
        *) command -v -- "$1" ;;
    esac
}

BT_PYTHON="$(absolute_interpreter "${BT_PYTHON}")"
MAIN_PYTHON="$(absolute_interpreter "${MAIN_PYTHON}")"
for interpreter in "${BT_PYTHON}" "${MAIN_PYTHON}"; do
    if [ ! -x "${interpreter}" ]; then
        echo "not an executable interpreter: ${interpreter}" >&2
        exit 2
    fi
done
# LumiBot writes a `logs/` directory relative to the process CWD during a real
# backtest run, independent of `save_logfile=False`; run it somewhere
# disposable so nothing lands in the repository.
SCRATCH="$(mktemp -d)"
trap 'rm -rf "${SCRATCH}"' EXIT

mkdir -p "${RESULTS}"

echo "== 1/5 rebuild the checked-in fixture set =="
"${MAIN_PYTHON}" "${HERE}/build_fixtures.py"

echo "== 2/5 backtest_runtime, in its own environment (each case run twice) =="
CASES=$("${MAIN_PYTHON}" -c "
import json, pathlib
manifest = json.loads((pathlib.Path('${HERE}/fixtures/parity_manifest.json')).read_text())
print(' '.join(case['case_id'] for case in manifest['cases']))
")
for case in ${CASES}; do
    input="${HERE}/fixtures/${case}.input.json"
    # `env -i` keeps the ambient environment (including any real credentials)
    # out of the child entirely; the distribution's own credential guard runs
    # regardless, this is belt and braces for a developer machine.
    (cd "${SCRATCH}" && env -i PATH="${PATH}" "${BT_PYTHON}" -m backtest_runtime \
        "${input}" "${SCRATCH}/${case}.run1.json" >/dev/null 2>&1)
    (cd "${SCRATCH}" && env -i PATH="${PATH}" "${BT_PYTHON}" -m backtest_runtime \
        "${input}" "${SCRATCH}/${case}.run2.json" >/dev/null 2>&1)
    if ! cmp -s "${SCRATCH}/${case}.run1.json" "${SCRATCH}/${case}.run2.json"; then
        echo "NON-DETERMINISTIC: ${case} produced two different result documents" >&2
        exit 1
    fi
    cp "${SCRATCH}/${case}.run1.json" "${RESULTS}/${case}.backtest_runtime.json"
    echo "  ${case}: byte-identical across two runs"
done

echo "== 3/5 backtesting/engine.py, in the main project's environment =="
"${MAIN_PYTHON}" "${HERE}/run_legacy_engine.py" "${RESULTS}"

echo "== 4/5 compare and classify =="
"${MAIN_PYTHON}" "${HERE}/compare_parity.py" "${RESULTS}" >/dev/null

echo "== 5/5 LumiBot fill-timing probe (isolated environment) =="
# Three fixtures, because one is not enough to separate the clocks: A is the
# undelayed default, E has gapped opens so a fill price identifies exactly one
# bar, and F is the delayed exact-parity case where the booking session moves.
{
    first=1
    for probe_case in case_a_buy_and_hold case_e_gapped_opens case_f_exact_entry_parity; do
        if [ "${first}" -eq 0 ]; then
            echo
            echo "================================================================"
            echo
        fi
        first=0
        (cd "${SCRATCH}" && env -i PATH="${PATH}" "${BT_PYTHON}" \
            "${HERE}/probe_lumibot_fill_timing.py" \
            "${HERE}/fixtures/${probe_case}.input.json" 2>/dev/null)
    done
} > "${RESULTS}/probe_output.txt"

echo
echo "artifacts written to ${RESULTS}"
ls -1 "${RESULTS}"
