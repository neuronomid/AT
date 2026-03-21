#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x "$ROOT_DIR/.venv/bin/at-agent-mt5-manual-replay" ]]; then
  exec "$ROOT_DIR/.venv/bin/at-agent-mt5-manual-replay" "$@"
fi

if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  echo "Expected $ROOT_DIR/.venv/bin/python to exist. Install dependencies first." >&2
  exit 1
fi

exec env PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" "$ROOT_DIR/.venv/bin/python" -m app.mt5_manual_replay "$@"
