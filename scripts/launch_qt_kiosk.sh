#!/usr/bin/env bash
# launch_qt_kiosk.sh — Launch Native Qt6 Frontend Module for NemoHeadUnit-Wireless.
#
# Usage:
#   bash scripts/launch_qt_kiosk.sh [--fullscreen]
#

set -euo pipefail

FULLSCREEN_FLAG=""
if [[ $# -gt 0 && "$1" == "--fullscreen" ]]; then
    FULLSCREEN_FLAG="--fullscreen"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(dirname "$SCRIPT_DIR")"

echo "[launch_qt_kiosk] Starting standalone Qt6 HeadUnit shell..."
cd "$WORKSPACE_DIR"

export PYTHONPATH="$WORKSPACE_DIR:$WORKSPACE_DIR/backend:${PYTHONPATH:-}"

micromamba run -n NemoHeadUnit-Wireless python -m backend.modules.qt6_gui.standalone $FULLSCREEN_FLAG
