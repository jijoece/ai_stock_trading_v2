#!/usr/bin/env bash
# Print the current migration position and a prompt for a fresh Claude session.
# Read-only: this never invokes Claude and never mutates the repository.
#
# To fix recorded review findings or start the next documented phase for
# real, run `python scripts/migration_helper.py run-claude` explicitly
# (add --dry-run to preview it first). That command is intentionally not
# wired into this script.
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
