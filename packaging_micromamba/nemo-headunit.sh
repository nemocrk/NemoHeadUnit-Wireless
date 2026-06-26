#!/usr/bin/env bash
# /opt/nemo-headunit/bin/nemo-headunit
#
# Launcher wrapper for NemoHeadUnit.
# Uses the Micromamba environment if available, falls back to system python3.

MICROMAMBA_ENV_PREFIX="/opt/nemo-headunit/env"
APP_MAIN="/opt/nemo-headunit/main.py"
DISPLAY=:0
DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket

if [ -x "${MICROMAMBA_ENV_PREFIX}/bin/python" ]; then
    exec "${MICROMAMBA_ENV_PREFIX}/bin/python" "${APP_MAIN}" "$@"
else
    exec python3 "${APP_MAIN}" "$@"
fi