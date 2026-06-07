#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python}"
if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
fi

echo "==> Installing dependencies..."
"$PYTHON" -m pip install --upgrade pip -q
"$PYTHON" -m pip install -r requirements.txt -r requirements-dev.txt -q

echo "==> Running tests..."
export SECRET_KEY="ci-test-secret-key-not-for-production"
export DATABASE_URL="sqlite:///:memory:"
export CORS_ORIGINS='["https://launchtrendai.vercel.app"]'
"$PYTHON" -m pytest tests/ -v

echo "All checks passed. Safe to push."
