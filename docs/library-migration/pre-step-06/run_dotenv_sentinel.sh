#!/usr/bin/env bash
# Reproducible .env-suppression proof for the pre-step before PR 6.
#
# Proves that `LUMIBOT_DISABLE_DOTENV=1`, set before `import lumibot`, prevents
# lumibot==4.5.78 / python-dotenv==1.2.2 from loading an operator's `.env` (or
# `.env.local`) out of the working directory or any of its ancestors.
#
# Runs entirely offline against fake sentinel credentials. It never reads, and
# must never be pointed at, a real `.env`: WORKDIR is a disposable directory
# whose ancestor chain the script checks for stray `.env` files first.
#
# Usage:
#   ./run_dotenv_sentinel.sh /path/to/disposable/workdir [existing-venv]
#
# With no second argument a fresh venv is built and `lumibot==4.5.78` is
# installed into it (309 packages, ~1.9 GB).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKDIR="${1:?usage: run_dotenv_sentinel.sh <workdir> [venv]}"
VENV="${2:-$WORKDIR/venv-b}"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"

mkdir -p "$WORKDIR"
WORKDIR="$(cd "$WORKDIR" && pwd)"

# Refuse to run anywhere an unrelated .env could be picked up by the upward
# walk — that would both corrupt the result and risk touching real secrets.
probe="$WORKDIR"
while :; do
  for stray in "$probe/.env" "$probe/.env.local"; do
    if [ -e "$stray" ]; then
      echo "REFUSING: pre-existing $stray would contaminate the proof" >&2
      exit 2
    fi
  done
  [ "$probe" = "/" ] && break
  probe="$(dirname "$probe")"
done

if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV"
  # NOTE: `python -m pip`, never `$VENV/bin/pip`.
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install "lumibot==4.5.78" > "$WORKDIR/install.log" 2>&1
fi
PY="$VENV/bin/python"

mkdir -p "$WORKDIR/scripts" "$WORKDIR/sentinel_cwd/nested_empty" "$WORKDIR/clean_cwd" "$WORKDIR/out"
cp "$HERE/guards.py" "$HERE/spike_backtest.py" "$WORKDIR/scripts/"
cp "$HERE/sentinel_dotenv/sentinel.env"       "$WORKDIR/sentinel_cwd/.env"
cp "$HERE/sentinel_dotenv/sentinel.env.local" "$WORKDIR/sentinel_cwd/.env.local"
export PYTHONPATH="$WORKDIR/scripts"

run() {  # label cwd suppress creds perturb
  local label=$1 cwd=$2 suppress=$3 creds=$4 perturb=$5
  ( cd "$cwd" \
    && SPIKE_SUPPRESS_DOTENV="$suppress" SPIKE_CREDS="$creds" SPIKE_PERTURB="$perturb" \
       "$PY" "$WORKDIR/scripts/spike_backtest.py" "$label" \
       > "$WORKDIR/out/stdout_$label.txt" 2> "$WORKDIR/out/stderr_$label.txt" )
  mv "$cwd/result_$label.json" "$WORKDIR/out/"
  echo "run $label: stdout_bytes=$(wc -c < "$WORKDIR/out/stdout_$label.txt" | tr -d ' ')"
}

#    label  cwd                              suppress creds   perturb
run  S0     "$WORKDIR/clean_cwd"             1        absent  0
run  S1     "$WORKDIR/sentinel_cwd"          0        absent  0
run  S2     "$WORKDIR/sentinel_cwd"          1        absent  0
run  S3     "$WORKDIR/sentinel_cwd"          1        absent  0
run  S4     "$WORKDIR/sentinel_cwd"          1        absent  1
run  S5     "$WORKDIR/sentinel_cwd/nested_empty" 0    absent  0

"$PY" "$HERE/summarize_dotenv_sentinel.py" "$WORKDIR/out"
