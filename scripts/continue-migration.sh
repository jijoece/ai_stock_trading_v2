#!/usr/bin/env bash
# Print the current migration position and a prompt for a fresh Claude session.
# Read-only: this never invokes Claude and never mutates the repository.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname -- "$script_dir")"

python="${PYTHON:-}"
if [[ -z "$python" ]]; then
  if [[ -x "$repo_root/.venv/bin/python" ]]; then
    python="$repo_root/.venv/bin/python"
  else
    python="python3"
  fi
fi

cd "$repo_root"
"$python" scripts/migration_helper.py status "$@"
echo
echo "--- prompt for a fresh Claude Code session ---"
echo
exec "$python" scripts/migration_helper.py continue-prompt "$@"
