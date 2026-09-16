#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cleanup() {
  echo ""
  echo "Stopping Sabrah processes..."
  if [[ -n "${BACKEND_PID:-}" ]]; then kill "${BACKEND_PID}" 2>/dev/null || true; fi
  if [[ -n "${AI_PID:-}" ]]; then kill "${AI_PID}" 2>/dev/null || true; fi
}
trap cleanup EXIT INT TERM

if [[ ! -d "${ROOT}/sabrah-travel-backend/.venv" ]] || [[ ! -d "${ROOT}/sabrah-ai/.venv" ]]; then
  echo "Virtualenvs missing. Run ./setup.sh first."
  exit 1
fi

if [[ ! -f "${ROOT}/sabrah-travel-backend/.env" ]] || [[ ! -f "${ROOT}/sabrah-ai/.env" ]]; then
  echo ".env files missing. Run ./setup.sh first, then fill in API keys."
  exit 1
fi

echo "Starting Sabrah Travel Backend on :8001"
cd "${ROOT}/sabrah-travel-backend"
# shellcheck disable=SC1091
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8001 &
BACKEND_PID=$!
deactivate

echo "Starting Sabrah AI on :8000"
cd "${ROOT}/sabrah-ai"
# shellcheck disable=SC1091
source .venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 &
AI_PID=$!
deactivate

echo ""
echo "Travel Backend: http://127.0.0.1:8001/health"
echo "Sabrah AI:      http://127.0.0.1:8000"
echo "Press Ctrl+C to stop both."
echo ""

wait