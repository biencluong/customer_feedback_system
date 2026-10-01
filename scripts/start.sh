#!/usr/bin/env bash
# Start the web UI in the foreground. Logs stay in this terminal.
# Stop with Ctrl+C or by closing/killing this terminal session.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "Missing .venv — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

echo "Starting Feedback Triage Agent on http://${HOST}:${PORT}"
echo "Stop with Ctrl+C"
exec .venv/bin/uvicorn feedback_agent.app.server:app --host "$HOST" --port "$PORT"
