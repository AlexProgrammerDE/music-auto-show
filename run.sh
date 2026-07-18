#!/usr/bin/env bash

set -euo pipefail

project_root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
binary="$project_root/target/release/music-auto-show"

if [[ ! -x "$binary" ]]; then
  printf 'Release binary not found: %s\nRun ./compile.sh first.\n' "$binary" >&2
  exit 1
fi

cd -- "$project_root"
exec "$binary" "$@"
