#!/usr/bin/env bash
set -euo pipefail
SCRIPT_PATH="${BASH_SOURCE[0]}"
if command -v readlink >/dev/null 2>&1; then SCRIPT_PATH="$(readlink -f -- "$SCRIPT_PATH")"; fi
ROOT="$(cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd -P)"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

if [[ "${1:-}" == "upscale" ]]; then
  shift
  exec "${PYTHON:-python3}" -m podlets.upscale_alias "$@"
fi

exec "${PYTHON:-python3}" -m podlets "$@"
