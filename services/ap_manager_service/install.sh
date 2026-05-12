#!/usr/bin/env bash
# install.sh — Manual installation of the NemoHeadUnit AP Manager D-Bus service
#
# ⚠️  USE THIS SCRIPT ONLY FOR MANUAL / DEVELOPMENT INSTALLS.
#     If you have a .deb package, use that instead:
#       sudo apt install ./nemo-headunit_*.deb
#     The .deb handles everything this script does, plus upgrade/remove lifecycle.
#
# Must be run as root from the repo root or the services/ap_manager_service/ dir:
#   sudo bash services/ap_manager_service/install.sh
#
# What this script does:
#   1. Creates the ap_manager Unix group
#   2. Copies ap_manager_service.py to /opt/nemo-headunit/services/ap_manager_service/
#   3. Installs D-Bus policy       → /etc/dbus-1/system.d/
#   4. Installs PolicyKit policy    → /usr/share/polkit-1/actions/
#   5. Installs systemd unit        → /etc/systemd/system/
#   6. Enables and starts the service
#
# To add a user to the ap_manager group after install:
#   sudo usermod -aG ap_manager <username>
#   (user must log out and back in for the group to take effect)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Paths
SERVICE_INSTALL_DIR="/opt/nemo-headunit/services/ap_manager_service"
DBUS_POLICY_DIR="/etc/dbus-1/system.d"
POLKIT_ACTIONS_DIR="/usr/share/polkit-1/actions"
SYSTEMD_DIR="/etc/systemd/system"

SERVICE_NAME="org.nemo.APManager.service"
GROUP_NAME="ap_manager"

# ---------------------------------------------------------------------------
echo "[1/6] Creating Unix group '${GROUP_NAME}' (if not exists)"
if ! getent group "${GROUP_NAME}" &>/dev/null; then
    groupadd --system "${GROUP_NAME}"
    echo "      Group '${GROUP_NAME}' created."
else
    echo "      Group '${GROUP_NAME}' already exists — skipped."
fi

# ---------------------------------------------------------------------------
echo "[2/6] Installing service to ${SERVICE_INSTALL_DIR}"
mkdir -p "${SERVICE_INSTALL_DIR}"
cp "${SCRIPT_DIR}/ap_manager_service.py" "${SERVICE_INSTALL_DIR}/"
chown root:root "${SERVICE_INSTALL_DIR}/ap_manager_service.py"
chmod 700       "${SERVICE_INSTALL_DIR}/ap_manager_service.py"
echo "      Done."

# ---------------------------------------------------------------------------
echo "[3/6] Installing D-Bus policy to ${DBUS_POLICY_DIR}"
cp "${SCRIPT_DIR}/org.nemo.APManager.conf" "${DBUS_POLICY_DIR}/"
chown root:root "${DBUS_POLICY_DIR}/org.nemo.APManager.conf"
chmod 644       "${DBUS_POLICY_DIR}/org.nemo.APManager.conf"
echo "      Reloading D-Bus..."
systemctl reload dbus 2>/dev/null || true   # non-fatal: will apply on next dbus start
echo "      Done."

# ---------------------------------------------------------------------------
echo "[4/6] Installing PolicyKit policy to ${POLKIT_ACTIONS_DIR}"
cp "${SCRIPT_DIR}/org.nemo.APManager.policy" "${POLKIT_ACTIONS_DIR}/"
chown root:root "${POLKIT_ACTIONS_DIR}/org.nemo.APManager.policy"
chmod 644       "${POLKIT_ACTIONS_DIR}/org.nemo.APManager.policy"
echo "      PolicyKit picks up changes automatically."
echo "      Done."

# ---------------------------------------------------------------------------
echo "[5/6] Installing systemd unit to ${SYSTEMD_DIR}"
# Patch ExecStart to match the actual install dir
sed "s|ExecStart=.*ap_manager_service.py|ExecStart=/opt/nemo-headunit/env/bin/python ${SERVICE_INSTALL_DIR}/ap_manager_service.py|g" \
    "${SCRIPT_DIR}/org.nemo.APManager.service" \
    > "${SYSTEMD_DIR}/${SERVICE_NAME}"
chown root:root "${SYSTEMD_DIR}/${SERVICE_NAME}"
chmod 644       "${SYSTEMD_DIR}/${SERVICE_NAME}"
systemctl daemon-reload
echo "      Done."

# ---------------------------------------------------------------------------
echo "[6/6] Enabling and starting service"
systemctl enable "${SERVICE_NAME}"
systemctl start  "${SERVICE_NAME}"

echo ""
echo "===================================================="
echo " ap_manager_service installed and running."
echo "===================================================="
echo ""
echo " Status : systemctl status ${SERVICE_NAME}"
echo " Logs   : journalctl -u ${SERVICE_NAME} -f"
echo ""
echo " To grant access to a user:"
echo "   sudo usermod -aG ${GROUP_NAME} <username>"
echo "   (user must log out and back in)"
echo ""
