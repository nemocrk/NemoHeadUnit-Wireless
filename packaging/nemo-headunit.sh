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
if [ -z "${XDG_RUNTIME_DIR:-}" ]; then
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
fi
mkdir -p "${XDG_RUNTIME_DIR}" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Display Server Detection & Startup (Wayland / X.Org)
# ---------------------------------------------------------------------------
DISPLAY_SERVER_PID=""

ensure_display_server() {
    # If over SSH, discover existing local user session XAUTHORITY if available
    if [ -z "${XAUTHORITY:-}" ]; then
        if [ -f "${HOME}/.Xauthority" ]; then
            export XAUTHORITY="${HOME}/.Xauthority"
        else
            local found_xauth
            found_xauth="$(find "${XDG_RUNTIME_DIR}" /tmp -maxdepth 2 -name "*xauth*" -o -name ".Xauthority" 2>/dev/null | head -n 1 || true)"
            if [ -n "$found_xauth" ]; then
                export XAUTHORITY="$found_xauth"
            fi
        fi
    fi

    # 1. Check if Wayland is already active on host
    if [ -z "${WAYLAND_DISPLAY:-}" ]; then
        if [ -S "${XDG_RUNTIME_DIR}/wayland-0" ]; then
            export WAYLAND_DISPLAY="wayland-0"
        elif [ -S "${XDG_RUNTIME_DIR}/wayland-1" ]; then
            export WAYLAND_DISPLAY="wayland-1"
        fi
    fi

    if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -S "${XDG_RUNTIME_DIR}/${WAYLAND_DISPLAY}" ]; then
        echo "[nemo-headunit] Active Wayland display detected: ${WAYLAND_DISPLAY}"
        export QT_QPA_PLATFORM="wayland-egl;wayland;xcb"
        return 0
    fi

    # 2. Check if X11 / X.Org is already active on host
    local x_socket="/tmp/.X11-unix/X0"
    if [ -S "$x_socket" ] || (command -v xset &>/dev/null && xset -display :0 q &>/dev/null); then
        echo "[nemo-headunit] Active X.Org display detected: :0"
        export DISPLAY=":0"
        export QT_QPA_PLATFORM="xcb"
        return 0
    fi

    # 3. Neither running: clean SSH inherited display vars before starting DRM/KMS compositor
    echo "[nemo-headunit] No active display server detected. Scanning for Wayland / X.Org on DRM..."
    unset DISPLAY
    unset WAYLAND_DISPLAY

    export WLR_BACKENDS="drm,libinput"
    export WLR_LIBINPUT_NO_DEVICES="1"
    export XDG_SESSION_TYPE="wayland"

    start_compositor() {
        local comp_cmd="$1"
        local comp_name="$(basename "$comp_cmd")"
        echo "[nemo-headunit] Starting ${comp_name} Wayland compositor via logind seat..."

        if command -v systemd-run &>/dev/null; then
            local run_prefix=""
            if [ "$EUID" -ne 0 ] && command -v sudo &>/dev/null; then
                run_prefix="sudo"
            fi
            $run_prefix systemd-run --unit="nemo-wayland-${comp_name}" --remain-after-exit=no \
                -p PAMName=login \
                -p TTYPath=/dev/tty7 \
                -p StandardInput=tty \
                -p User="${USER:-$(id -un)}" \
                -p WorkingDirectory="${HOME}" \
                -E LIBSEAT_BACKEND=seatd \
                -E XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR}" \
                dbus-run-session ${comp_cmd} &>/dev/null &
        else
            WLR_BACKENDS=drm WLR_LIBINPUT_NO_DEVICES=1 dbus-run-session ${comp_cmd} &
            DISPLAY_SERVER_PID=$!
        fi

        local count=0
        while [ ! -S "${XDG_RUNTIME_DIR}/wayland-0" ] && [ $count -lt 35 ]; do
            sleep 0.2
            count=$((count + 1))
        done
        if [ -S "${XDG_RUNTIME_DIR}/wayland-0" ]; then
            export WAYLAND_DISPLAY="wayland-0"
            export QT_QPA_PLATFORM="wayland;xcb"
            echo "[nemo-headunit] ${comp_name} started successfully (wayland-0 ready)"
            return 0
        fi
        return 1
    }

    # Check Wayland compositors in order: labwc -> cage -> weston -> sway
    if command -v labwc &>/dev/null && start_compositor "labwc"; then
        return 0
    elif command -v cage &>/dev/null && start_compositor "cage -d"; then
        return 0
    elif command -v weston &>/dev/null && start_compositor "weston --backend=drm-backend.so"; then
        return 0
    elif command -v sway &>/dev/null && start_compositor "sway"; then
        return 0
    fi

    # Check X.Org
    if command -v Xorg &>/dev/null || command -v X &>/dev/null; then
        local x_bin="$(command -v Xorg || command -v X)"
        echo "[nemo-headunit] Starting X.Org server (${x_bin}) on :0..."
        if [ "$EUID" -ne 0 ] && command -v sudo &>/dev/null; then
            sudo systemd-run --unit="nemo-xorg" --remain-after-exit=no \
                -p PAMName=login \
                -p TTYPath=/dev/tty1 \
                -p User="${USER:-$(id -un)}" \
                "${x_bin}" :0 vt1 -keeptty -novtswitch &>/dev/null &
        else
            "${x_bin}" :0 vt1 -keeptty -novtswitch &>/dev/null &
            DISPLAY_SERVER_PID=$!
        fi
        local count=0
        while [ ! -S "/tmp/.X11-unix/X0" ] && [ $count -lt 30 ]; do
            sleep 0.2
            count=$((count + 1))
        done
        if [ -S "/tmp/.X11-unix/X0" ]; then
            export DISPLAY=":0"
            export QT_QPA_PLATFORM="xcb"
            return 0
        fi
    elif command -v startx &>/dev/null; then
        echo "[nemo-headunit] Starting X11 via startx..."
        startx &
        DISPLAY_SERVER_PID=$!
        sleep 2
        export DISPLAY=":0"
        export QT_QPA_PLATFORM="xcb"
        return 0
    fi

    echo "[nemo-headunit] Warning: Could not auto-start Wayland or X.Org. Falling back to eglfs/linuxfb platform."
    export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-eglfs}"
}

ensure_display_server

export DISPLAY="${DISPLAY:-:0}"
export QT_OPENGL="desktop"
export QSG_RHI_BACKEND="opengl"
export QT_WAYLAND_CLIENT_BUFFER_INTEGRATION="${QT_WAYLAND_CLIENT_BUFFER_INTEGRATION:-wayland-egl}"
# Drivers and VA-API paths (if not already set)
export LIBVA_DRIVERS_PATH="${LIBVA_DRIVERS_PATH:-/usr/lib/dri}"
export MESA_LOADER_DRIVER_PATH="${MESA_LOADER_DRIVER_PATH:-/usr/lib/dri}"
export LIBGL_DRIVERS_PATH="${LIBGL_DRIVERS_PATH:-/usr/lib/dri}"
export LD_LIBRARY_PATH=/usr/lib:$LD_LIBRARY_PATH


# Resolve Python binary
if [ -x "${MICROMAMBA_ENV_PREFIX}/bin/python" ]; then
    PYTHON_BIN="${MICROMAMBA_ENV_PREFIX}/bin/python"
else
    PYTHON_BIN="python3"
fi

# Auto-forward output to systemd-cat/journald if not already running in a journal stream
if [ -z "${JOURNAL_STREAM:-}" ] && [ -z "${NEMO_NO_SYSTEMD_CAT:-}" ] && command -v systemd-cat &>/dev/null; then
    exec systemd-cat -t nemo-headunit "$0" "$@"
fi

# 1. Start Python backend (runs native Qt6 GUI by default as Priority 5)
echo "[nemo-headunit] Starting NemoHeadUnit (backend + Qt6 GUI)..."
"${PYTHON_BIN}" "${APP_MAIN}" "$@" &
BACKEND_PID=$!

# Ensure backend process and WiFi AP are terminated when launcher exits or crashes
cleanup() {
    echo "[nemo-headunit] Shutting down backend (PID: ${BACKEND_PID})..."
    kill -TERM "${BACKEND_PID}" 2>/dev/null || true
    wait "${BACKEND_PID}" 2>/dev/null || true

    # Clean up any orphaned AP state on exit/crash
    busctl call org.nemo.APManager /org/nemo/APManager org.nemo.APManager Stop 2>/dev/null || true

    # Stop display server if we started it
    if [ -n "${DISPLAY_SERVER_PID}" ]; then
        kill -TERM "${DISPLAY_SERVER_PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM HUP

# 2. If browser kiosk mode is explicitly requested via --browser, --web, or KIOSK_MODE=browser
if [[ "$*" == *"--browser"* ]] || [[ "$*" == *"--web"* ]] || [ "${KIOSK_MODE:-qt6}" = "browser" ]; then
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
    if [ -f "${KIOSK_SCRIPT}" ]; then
        echo "[nemo-headunit] Launching Kiosk Web Browser..."
        bash "${KIOSK_SCRIPT}" --url "http://localhost:8000" "$@"
    fi
fi

# Default: Wait for backend orchestrator process (which hosts Qt6 GUI)
wait "${BACKEND_PID}"