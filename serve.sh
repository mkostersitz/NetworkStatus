#!/bin/bash
# Start the NetMon web server.
# Usage: ./serve.sh [--host 0.0.0.0] [--port 8080]
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/.venv/bin/uvicorn" web.app:app \
  --host "${NETMON_HOST:-0.0.0.0}" \
  --port "${NETMON_PORT:-8080}" \
  --app-dir "$SCRIPT_DIR" \
  "$@"
