#!/usr/bin/env bash
# /opt/nemo-headunit/bin/nemo-headunit
#
# Launcher wrapper for NemoHeadUnit.
# Uses the Conda env Python if available, falls back to system python3.

CONDA_PYTHON="/opt/nemo-headunit/env/bin/python"
APP_MAIN="/opt/nemo-headunit/main.py"
export DISPLAY="${DISPLAY:-:0}"
export DBUS_SYSTEM_BUS_ADDRESS="${DBUS_SYSTEM_BUS_ADDRESS:-unix:path=/run/dbus/system_bus_socket}"

if [ -x "${CONDA_PYTHON}" ]; then
    exec "${CONDA_PYTHON}" "${APP_MAIN}" "$@"
else
    exec python3 "${APP_MAIN}" "$@"
fi
