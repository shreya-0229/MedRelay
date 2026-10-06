#!/usr/bin/env bash
# MedRelay full-stack — one-command start (Linux/Mac).
# Installs backend deps into backend/.venv (avoids PEP-668 externally-managed
# pip issues), builds the frontend if dist/ is missing, then starts the
# backend which serves both the API and the dashboard on port 8000.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "==> Backend dependencies..."
cd "$ROOT/backend"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt

echo "==> Frontend (build only if dist/ is missing)..."
cd "$ROOT/frontend"
if [ ! -d "dist" ]; then
  [ -d "node_modules" ] || npm install
  npm run build
else
  echo "    dist/ already present — skipping build."
fi

echo "==> Starting MedRelay: open http://localhost:8000"
cd "$ROOT/backend"
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
