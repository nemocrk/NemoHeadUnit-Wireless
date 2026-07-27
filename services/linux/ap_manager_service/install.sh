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
#   3. Installs D-Bus policy            → /etc/dbus-1/system.d/
#   4. Installs D-Bus activation file   → /usr/share/dbus-1/system-services/
#   5. Installs PolicyKit .policy       → /usr/share/polkit-1/actions/
#   6. Installs PolicyKit .rules (BlueZ) → /etc/polkit-1/rules.d/
#   7. Installs systemd unit            → /etc/systemd/system/
#   8. Enables and starts the service
#   9. Runs platform-specific hardware fixes (hardware_fixes/run_hardware_fixes.sh)
#
# To add a user to the ap_manager group after install:
#   sudo usermod -aG ap_manager <username>
#   (user must log out and back in for the group to take effect)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# Paths
SERVICE_INSTALL_DIR="/opt/nemo-headunit/services/ap_manager_service"
DBUS_POLICY_DIR="/etc/dbus-1/system.d"
DBUS_SERVICES_DIR="/usr/share/dbus-1/system-services"
POLKIT_ACTIONS_DIR="/usr/share/polkit-1/actions"
POLKIT_RULES_DIR="/etc/polkit-1/rules.d"
SYSTEMD_DIR="/etc/systemd/system"
HW_FIX_RUNNER="${REPO_ROOT}/packaging/hardware_fixes/run_hardware_fixes.sh"

SERVICE_NAME="org.nemo.APManager.service"
GROUP_NAME="ap_manager"

# ---------------------------------------------------------------------------
echo "[1/9] Creating Unix group '${GROUP_NAME}' (if not exists)"
if ! getent group "${GROUP_NAME}" &>/dev/null; then
    groupadd --system "${GROUP_NAME}"
    echo "      Group '${GROUP_NAME}' created."
else
    echo "      Group '${GROUP_NAME}' already exists — skipped."
fi

# ---------------------------------------------------------------------------
echo "[2/9] Installing service to ${SERVICE_INSTALL_DIR}"
mkdir -p "${SERVICE_INSTALL_DIR}"
cp "${SCRIPT_DIR}/ap_manager_service.py" "${SERVICE_INSTALL_DIR}/"
chown root:root "${SERVICE_INSTALL_DIR}/ap_manager_service.py"
chmod 700       "${SERVICE_INSTALL_DIR}/ap_manager_service.py"
echo "      Done."

# ---------------------------------------------------------------------------
echo "[3/9] Installing D-Bus policy to ${DBUS_POLICY_DIR}"
cp "${SCRIPT_DIR}/org.nemo.APManager.conf" "${DBUS_POLICY_DIR}/"
chown root:root "${DBUS_POLICY_DIR}/org.nemo.APManager.conf"
chmod 644       "${DBUS_POLICY_DIR}/org.nemo.APManager.conf"
echo "      Reloading D-Bus..."
systemctl reload dbus 2>/dev/null || true
echo "      Done."

# ---------------------------------------------------------------------------
echo "[4/9] Installing D-Bus activation file to ${DBUS_SERVICES_DIR}"
# This file tells the bus daemon that org.nemo.APManager is a legitimate
# root-owned service.  Without it polkitd rejects CheckAuthorization calls
# with AccessDenied because it cannot verify the sender as a trusted owner.
mkdir -p "${DBUS_SERVICES_DIR}"
cp "${SCRIPT_DIR}/org.nemo.APManager.dbus-service" \
   "${DBUS_SERVICES_DIR}/org.nemo.APManager.service"
chown root:root "${DBUS_SERVICES_DIR}/org.nemo.APManager.service"
chmod 644       "${DBUS_SERVICES_DIR}/org.nemo.APManager.service"
echo "      Reloading D-Bus..."
systemctl reload dbus 2>/dev/null || true
echo "      Done."

# ---------------------------------------------------------------------------
echo "[5/9] Installing PolicyKit policy to ${POLKIT_ACTIONS_DIR}"
# NOTE: filename must be lowercase to match action IDs (polkitd 127 is
# case-sensitive on filenames).  Source file may still be named with
# camelcase in the repo; we install it with the canonical lowercase name.
cp "${SCRIPT_DIR}/org.nemo.APManager.policy" \
   "${POLKIT_ACTIONS_DIR}/org.nemo.apmanager.policy"
chown root:root "${POLKIT_ACTIONS_DIR}/org.nemo.apmanager.policy"
chmod 644       "${POLKIT_ACTIONS_DIR}/org.nemo.apmanager.policy"
echo "      PolicyKit picks up changes automatically."
echo "      Done."

# ---------------------------------------------------------------------------
echo "[6/9] Installing PolicyKit JS rules to ${POLKIT_RULES_DIR}"
mkdir -p "${POLKIT_RULES_DIR}"
cp "${REPO_ROOT}/packaging/org.nemo.bluetooth.rules" "${POLKIT_RULES_DIR}/"
chown root:root "${POLKIT_RULES_DIR}/org.nemo.bluetooth.rules"
chmod 644       "${POLKIT_RULES_DIR}/org.nemo.bluetooth.rules"
echo "      Done."

# ---------------------------------------------------------------------------
echo "[7/9] Installing systemd unit to ${SYSTEMD_DIR}"
# Patch ExecStart to match the actual install dir
sed "s|ExecStart=.*ap_manager_service.py|ExecStart=/opt/nemo-headunit/env/bin/python ${SERVICE_INSTALL_DIR}/ap_manager_service.py|g" \
    "${SCRIPT_DIR}/org.nemo.APManager.service" \
    > "${SYSTEMD_DIR}/${SERVICE_NAME}"
chown root:root "${SYSTEMD_DIR}/${SERVICE_NAME}"
chmod 644       "${SYSTEMD_DIR}/${SERVICE_NAME}"
systemctl daemon-reload
echo "      Done."

# ---------------------------------------------------------------------------
echo "[8/9] Enabling and starting service"
systemctl enable "${SERVICE_NAME}"
systemctl start  "${SERVICE_NAME}"
echo "      Done."

# ---------------------------------------------------------------------------
echo "[9/9] Platform-specific hardware fixes"
if [ -f "${HW_FIX_RUNNER}" ]; then
    chmod +x "${HW_FIX_RUNNER}"
    bash "${HW_FIX_RUNNER}"
else
    echo "      Hardware fix runner non trovato in ${HW_FIX_RUNNER} — skipped."
fi

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
