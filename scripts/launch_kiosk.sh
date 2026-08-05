#!/usr/bin/env bash
# launch_kiosk.sh — Launch browser in Kiosk Mode for NemoHeadUnit UI.
#
# Usage:
#   bash scripts/launch_kiosk.sh [--browser <binary>] [--url <URL>] [--temp-profile]
#   KIOSK_BROWSER=chromium bash scripts/launch_kiosk.sh
#

set -euo pipefail

# Force legacy i965 Intel VA-API driver for hardware H.264 video decoding
export LIBVA_DRIVER_NAME="${LIBVA_DRIVER_NAME:-i965}"
export LIBVA_DRIVERS_PATH="${LIBVA_DRIVERS_PATH:-/usr/lib/x86_64-linux-gnu/dri:/usr/lib/dri}"

URL="http://localhost:8000"
SELECTED_BROWSER="${KIOSK_BROWSER:-}"
USE_TEMP_PROFILE=0
ENABLE_DEVTOOLS=0

show_help() {
  echo "Usage: $0 [--browser <binary>] [--url <URL>] [--temp-profile] [--dev]"
  echo ""
  echo "Options:"
  echo "  --browser <name>  Specify browser executable (google-chrome, chromium, firefox, microsoft-edge-stable, etc.)"
  echo "  --url <URL>       Target URL to open (default: http://localhost:8000)"
  echo "  --temp-profile    Use a temporary browser user data directory"
  echo "  --dev, --devtools Enable Chrome DevTools (auto-open devtools & remote debugging port 9222)"
  echo "  --help            Show this help message"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --browser|-b)
      SELECTED_BROWSER="$2"
      shift 2
      ;;
    --url|-u)
      URL="$2"
      shift 2
      ;;
    --temp-profile)
      USE_TEMP_PROFILE=1
      shift
      ;;
    --dev|--devtools|-d)
      ENABLE_DEVTOOLS=1
      shift
      ;;
    --help|-h)
      show_help
      ;;
    *)
      if [[ "$1" =~ ^http ]]; then
        URL="$1"
      fi
      shift
      ;;
  esac
done

# Browser detection logic
detect_browser() {
  if [ -n "$SELECTED_BROWSER" ]; then
    if command -v "$SELECTED_BROWSER" &>/dev/null; then
      echo "$SELECTED_BROWSER"
      return 0
    else
      echo "Error: Requested browser '$SELECTED_BROWSER' is not installed or not in PATH." >&2
      exit 1
    fi
  fi

  for candidate in surf falkon chromium-browser chromium google-chrome google-chrome-stable microsoft-edge-stable microsoft-edge firefox; do
    if command -v "$candidate" &>/dev/null; then
      echo "$candidate"
      return 0
    fi
  done

  echo "Error: No supported browser found in system PATH (firefox, falkon, chromium-browser, chromium, google-chrome, surf)." >&2
  exit 1
}

BROWSER_BIN="$(detect_browser)"
echo "[launch_kiosk] Using browser: ${BROWSER_BIN}"
echo "[launch_kiosk] Target URL:    ${URL}"

EXTRA_FLAGS=()
if [ "$USE_TEMP_PROFILE" -eq 1 ]; then
  PROFILE_DIR="/tmp/nemo-kiosk-profile-$$"
  mkdir -p "$PROFILE_DIR"
  EXTRA_FLAGS+=("--user-data-dir=${PROFILE_DIR}")
fi

if [ "$ENABLE_DEVTOOLS" -eq 1 ]; then
  echo "[launch_kiosk] DevTools enabled (--auto-open-devtools-for-tabs, --remote-debugging-port=9222)"
  EXTRA_FLAGS+=(
    "--auto-open-devtools-for-tabs"
    "--remote-debugging-port=9222"
  )
fi

if [[ "$BROWSER_BIN" == *"falkon"* ]]; then
  # Falkon (QtWebEngine) kiosk flags: -e (fullscreen / kiosk), -r (open URL)
  export DISPLAY="${DISPLAY:-:0}"
  exec "$BROWSER_BIN" -e -r "${URL}"
elif [[ "$BROWSER_BIN" == *"surf"* ]]; then
  # Surf (suckless WebKitGTK) kiosk flags: -F (fullscreen), -K (kiosk mode)
  # Force WebKitGTK hardware accelerated compositing, WebGL, & GStreamer rank priorities
  export WEBKIT_FORCE_COMPOSITING_MODE=1
  export WEBKIT_DISABLE_COMPOSITING_MODE=0
  export WEBKIT_ENABLE_WEBGL=1
  export WEBKIT_WEBGL_ACCELERATION=1
  export WEBKIT_GST_DECODER_RANK="${WEBKIT_GST_DECODER_RANK:-primary}"
  export GDK_BACKEND="${GDK_BACKEND:-x11}"
  export LIBGL_DRIVERS_PATH="${LIBGL_DRIVERS_PATH:-/usr/lib/x86_64-linux-gnu/dri:/usr/lib/dri:/opt/nemo-headunit/env/lib/dri}"
  exec "$BROWSER_BIN" -F -K "${URL}"
elif [[ "$BROWSER_BIN" == *"firefox"* ]]; then
  export MOZ_CRASHREPORTER_DISABLE=1
  export MOZ_WEBRENDER=1
  export MOZ_DISABLE_RDD_SANDBOX=1
  exec "$BROWSER_BIN" --no-remote -p NemoHeadUnit-Wireless --kiosk "${URL}"
else
  # Chromium / Chrome / Edge flags optimized for low-end device RAM footprint & WebCodecs stream playback
  exec "$BROWSER_BIN" \
    --noerrdialogs \
    --disable-infobars \
    --enable-low-end-device-mode \
    --disable-extensions \
    --disable-component-update \
    --disable-background-networking \
    --disable-gcm \
    --disable-sync \
    --disable-translate \
    --no-first-run \
    --disable-gpu-shader-disk-cache \
    --gpu-program-cache-size-kb=0 \
    --num-raster-threads=2 \
    --autoplay-policy=no-user-gesture-required \
    --check-for-update-interval=31536000 \
    --enable-gpu-rasterization \
    --ignore-gpu-blocklist \
    --js-flags="--max-old-space-size=128" \
    --disable-client-side-phishing-detection \
    --safebrowsing-disable-auto-update \
    --disable-breakpad \
    --disable-dev-shm-usage \
    --disable-restore-session-state \
    --no-default-browser-check \
    --in-process-gpu \
    --renderer-process-limit=1 \
    --disk-cache-size=1 \
    --disk-cache-dir=/dev/null \
    --disable-crash-reporter \
    --disable-smooth-scrolling \
    --disable-speech-api \
    --enable-logging=stderr --v=0 --vmodule=media/*=2,gpu/*=2 \
    --disable-features=Translate,PushMessaging,OverscrollHistoryNavigation,MediaRouter,AutofillServerCommunication,BackForwardCache,DialMediaRouteProvider,OptimizationHints \
    "${EXTRA_FLAGS[@]}" \
    "${URL}"
fi
