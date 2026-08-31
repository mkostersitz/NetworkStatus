#!/bin/bash
# Thin launcher — activates the venv and runs the CLI.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/cli.py" "$@"
