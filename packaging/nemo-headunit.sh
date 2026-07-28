#!/usr/bin/env bash
# /opt/nemo-headunit/bin/nemo-headunit
#
# Main launcher wrapper for NemoHeadUnit.
# Starts the Python backend orchestrator and launches the Kiosk UI browser.

MICROMAMBA_ENV_PREFIX="/opt/nemo-headunit/env"
APP_DIR="/opt/nemo-headunit"
APP_MAIN="${APP_DIR}/main.py"
KIOSK_SCRIPT="${APP_DIR}/scripts/launch_kiosk.sh"

if [ -z "${DBUS_SYSTEM_BUS_ADDRESS:-}" ]; then
    export DBUS_SYSTEM_BUS_ADDRESS="unix:path=/run/dbus/system_bus_socket"
fi
export DISPLAY="${DISPLAY:-:0}"
export LIBVA_DRIVERS_PATH="${LIBVA_DRIVERS_PATH:-/usr/lib/x86_64-linux-gnu/dri:/usr/lib/dri:/opt/nemo-headunit/env/lib/dri}"

# Resolve Python binary
if [ -x "${MICROMAMBA_ENV_PREFIX}/bin/python" ]; then
    PYTHON_BIN="${MICROMAMBA_ENV_PREFIX}/bin/python"
else
    PYTHON_BIN="python3"
fi

# 1. Start Python backend in background
echo "[nemo-headunit] Starting backend orchestrator..."
"${PYTHON_BIN}" "${APP_MAIN}" "$@" &
BACKEND_PID=$!

# Ensure backend process is terminated when launcher exits
cleanup() {
    echo "[nemo-headunit] Shutting down backend (PID: ${BACKEND_PID})..."
    kill -TERM "${BACKEND_PID}" 2>/dev/null || true
    wait "${BACKEND_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 2. Wait for Gateway Proxy HTTP server to be ready on port 8000
echo "[nemo-headunit] Waiting for backend gateway on http://127.0.0.1:8000..."
MAX_WAIT=20
WAIT_COUNT=0
while ! curl -s http://127.0.0.1:8000/api/config/ &>/dev/null; do
    sleep 0.5
    WAIT_COUNT=$((WAIT_COUNT + 1))
    if [ "$WAIT_COUNT" -ge "$((MAX_WAIT * 2))" ]; then
        echo "[nemo-headunit] Warning: Backend gateway check timed out. Proceeding to launch kiosk UI..."
        break
    fi
done

# 3. Launch Kiosk Browser
if [ -f "${KIOSK_SCRIPT}" ]; then
    echo "[nemo-headunit] Launching Kiosk UI..."
    bash "${KIOSK_SCRIPT}" --url "http://localhost:8000"
else
    echo "[nemo-headunit] Kiosk script not found at ${KIOSK_SCRIPT}. Waiting on backend process..."
    wait "${BACKEND_PID}"
fi