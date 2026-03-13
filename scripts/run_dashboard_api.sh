#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -x "$ROOT_DIR/.venv/bin/at-agent-dashboard-api" ]]; then
  echo "Expected $ROOT_DIR/.venv/bin/at-agent-dashboard-api to exist. Install dependencies first." >&2
  exit 1
fi

exec "$ROOT_DIR/.venv/bin/at-agent-dashboard-api"
