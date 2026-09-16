#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

setup_project() {
  local dir="$1"
  echo "==> Setting up ${dir}"
  cd "${ROOT}/${dir}"
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install --upgrade pip
  pip install -r requirements.txt
  if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "    Created ${dir}/.env from .env.example"
  else
    echo "    ${dir}/.env already exists — left unchanged"
  fi
  deactivate
}

setup_project "sabrah-travel-backend"
setup_project "sabrah-ai"

echo ""
echo "Setup complete."
echo "Next:"
echo "  1. Edit sabrah-ai/.env (OPENAI_API_KEY, ELEVENLABS_*, TRAVEL_BACKEND_API_KEY)"
echo "  2. Edit sabrah-travel-backend/.env (API_KEY must match TRAVEL_BACKEND_API_KEY)"
echo "  3. Run: ./run-dev.sh"
echo "  4. Open: http://127.0.0.1:8000"
