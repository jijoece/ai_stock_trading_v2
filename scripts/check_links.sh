#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(dirname -- "$script_dir")"

if ! command -v lychee >/dev/null 2>&1; then
  echo "lychee is required; install it with 'brew install lychee' or see https://github.com/lycheeverse/lychee#installation" >&2
  exit 127
fi

cd "$repo_root"
exec lychee --root-dir "$repo_root" './**/*.md'
