#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Running headless Teladoc session sync..."
"$DIR/.venv/bin/python" "$DIR/livongo_session_export.py" --headless --upload-url "http://192.168.50.116:8099/upload"
echo "[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] Completed Teladoc session sync."
