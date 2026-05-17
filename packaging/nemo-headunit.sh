#!/usr/bin/env bash
# /opt/nemo-headunit/bin/nemo-headunit
#
# Launcher wrapper for NemoHeadUnit.
# Uses the Conda env Python if available, falls back to system python3.

CONDA_PYTHON="/opt/nemo-headunit/env/bin/python"
APP_MAIN="/opt/nemo-headunit/v2/main.py"

if [ -x "${CONDA_PYTHON}" ]; then
    exec "${CONDA_PYTHON}" "${APP_MAIN}" "$@"
else
    exec python3 "${APP_MAIN}" "$@"
fi
