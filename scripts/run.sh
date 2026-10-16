#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
. .venv/bin/activate
. scripts/env.sh
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
