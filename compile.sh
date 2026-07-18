#!/usr/bin/env bash

set -euo pipefail

project_root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

for required_command in bun cargo; do
  if ! command -v "$required_command" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "$required_command" >&2
    exit 127
  fi
done

cd -- "$project_root"

bun install --frozen-lockfile
bun install --cwd frontend --frozen-lockfile
exec bun run build
