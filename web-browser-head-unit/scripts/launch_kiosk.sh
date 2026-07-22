#!/usr/bin/env bash
# launch_kiosk.sh — Kiosk launcher for Lubuntu (Intel Atom Bay Trail i965 VA-API)
# Strict compliance with docs/new-pattern.md for sub-50ms ultra-low latency & 2GB RAM limits

set -euo pipefail

# Force legacy Intel VA-API driver for Bay Trail HD Graphics
export LIBVA_DRIVER_NAME=i965

TARGET_URL="${1:-http://127.0.0.1:8000}"

echo "[Kiosk] Launching WebCodecs browser kiosk pointing to ${TARGET_URL}..."

# Launch Chromium in app mode with forced hardware acceleration and capped memory
if command -v chromium-browser &>/dev/null; then
    BROWSER_BIN="chromium-browser"
elif command -v chromium &>/dev/null; then
    BROWSER_BIN="chromium"
else
    BROWSER_BIN="google-chrome"
fi

exec "${BROWSER_BIN}" \
    --app="${TARGET_URL}" \
    --kiosk \
    --enable-accelerated-video-decode \
    --use-gl=egl \
    --disable-dev-shm-usage \
    --js-flags="--max-old-space-size=256" \
    --autoplay-policy=no-user-gesture-required
