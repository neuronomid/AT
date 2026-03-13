#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
  echo "Expected frontend/node_modules to exist. Run npm --prefix frontend install first." >&2
  exit 1
fi

exec npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173
