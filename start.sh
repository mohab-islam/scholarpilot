#!/usr/bin/env bash
# Local launcher for macOS / Linux. Creates a virtualenv on first run, then serves http://127.0.0.1:8000
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r backend/requirements.txt
cd backend
echo "Open http://127.0.0.1:8000"
exec ../.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
