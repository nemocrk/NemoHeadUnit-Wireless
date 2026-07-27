#!/usr/bin/env bash
# Launch Chrome / Chromium in Kiosk Mode pointing to the local head unit UI.

URL="${1:-http://localhost:8080}"

if command -v google-chrome &>/dev/null; then
    BROWSER="google-chrome"
elif command -v chromium-browser &>/dev/null; then
    BROWSER="chromium-browser"
elif command -v chromium &>/dev/null; then
    BROWSER="chromium"
else
    echo "Error: No supported browser found (google-chrome, chromium)."
    exit 1
fi

echo "Launching ${BROWSER} in kiosk mode at ${URL}..."
exec "${BROWSER}" --kiosk --noerrdialogs --disable-infobars --autoplay-policy=no-user-gesture-required "${URL}"
