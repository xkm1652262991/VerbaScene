#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export PYTHONPATH="${PYTHONPATH:-$SCRIPT_DIR}"
export PYTHON_BIN="${PYTHON_BIN:-python}"
export TORCHRUN_BIN="${TORCHRUN_BIN:-torchrun}"
exec "${UVICORN_PYTHON_BIN:-python}" -m uvicorn app.main:app --host "${WAN_API_HOST:-0.0.0.0}" --port "${WAN_API_PORT:-7862}"
