#!/bin/sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"
if [ -n "${JUSTREAD_PYTHON:-}" ]; then
  PYTHON_BIN=$JUSTREAD_PYTHON
elif [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
else
  PYTHON_BIN="$PROJECT_DIR/backend/.venv/bin/python"
fi
if [ ! -x "$PYTHON_BIN" ]; then
  printf '%s\n' "Python environment missing: create .venv or backend/.venv and install backend/requirements-dev.txt first." >&2
  exit 1
fi
export JUSTREAD_STATIC_DIR=${JUSTREAD_STATIC_DIR:-"$PROJECT_DIR/dist"}
exec "$PYTHON_BIN" -m uvicorn just_read.app:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
