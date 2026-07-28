#!/usr/bin/env bash
# deploy_remote_deb.sh — Builds, transfers, installs, and launches the NemoHeadUnit Debian package on a remote host.
#
# Usage:
#   bash scripts/deploy_remote_deb.sh [--arch amd64|arm64] <user> <host>
#
# Example:
#   bash scripts/deploy_remote_deb.sh nemo 192.168.1.50
#   bash scripts/deploy_remote_deb.sh --arch arm64 nemo 192.168.1.50
#

set -euo pipefail

ARCH="amd64"
REMOTE_USER=""
REMOTE_HOST=""

show_help() {
  echo "Usage: $0 [--arch amd64|arm64] <user> <host>"
  echo ""
  echo "Options:"
  echo "  --arch       Target architecture (amd64 or arm64, default: amd64)"
  echo "  --help       Show this help message"
  echo ""
  echo "Example:"
  echo "  $0 --arch amd64 nemo 192.168.1.50"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --arch)
      ARCH="$2"
      shift 2
      ;;
    --help|-h)
      show_help
      ;;
    *)
      if [ -z "$REMOTE_USER" ]; then
        REMOTE_USER="$1"
      elif [ -z "$REMOTE_HOST" ]; then
        REMOTE_HOST="$1"
      fi
      shift
      ;;
  esac
done

if [ -z "$REMOTE_USER" ] || [ -z "$REMOTE_HOST" ]; then
  echo "Error: Missing remote user or host." >&2
  show_help
fi

REMOTE="${REMOTE_USER}@${REMOTE_HOST}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=================================================="
echo " Deploying NemoHeadUnit Debian Package over SSH"
echo " Target : $REMOTE"
echo " Arch   : $ARCH"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================================="
echo ""

# ---------------------------------------------------------------------------
# Step 1: Build Debian package locally
# ---------------------------------------------------------------------------
echo "[1/4] Building Debian package locally..."
bash "${REPO_ROOT}/packaging/build_deb.sh" --arch "${ARCH}" --output-dir dist
echo "[OK] Local package build completed."
echo ""

VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
DEB_FILENAME="nemo-headunit_${VERSION}_${ARCH}.deb"
DEB_PATH="${REPO_ROOT}/dist/${DEB_FILENAME}"

if [ ! -f "${DEB_PATH}" ]; then
  echo "Error: Package not found at ${DEB_PATH}" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Step 2: Copy package to remote host
# ---------------------------------------------------------------------------
echo "[2/4] Copying ${DEB_FILENAME} to ${REMOTE}:/tmp/..."
rsync -avz -e ssh "${DEB_PATH}" "${REMOTE}:/tmp/${DEB_FILENAME}"
echo "[OK] Package transferred."
echo ""

# ---------------------------------------------------------------------------
# Step 3: Install .deb on remote host via APT (postinst triggers hardware fixes)
# ---------------------------------------------------------------------------
echo "[3/4] Checking APT cache age and installing package on remote machine..."

# Controlla la data di modifica reale della cartella della cache di APT (/var/cache/apt)
SSH_COMMAND="if [ -z \"\$(find /var/cache/apt -maxdepth 0 -mmin -1440 2>/dev/null)\" ]; then \
    echo 'Cache vecchia o inesistente. Aggiorno APT...'; sudo apt-get update; \
else \
    echo 'Cache APT recente. Salto l update.'; \
fi && sudo apt-get install -f -y /tmp/${DEB_FILENAME}"

ssh -t "${REMOTE}" "$SSH_COMMAND"

echo "[OK] Remote package installation finished."
echo ""

# ---------------------------------------------------------------------------
# Step 4: Launch application via nohup and stream live logs
# ---------------------------------------------------------------------------
echo "[4/4] Starting NemoHeadUnit and streaming live output..."
echo "      (Press Ctrl+C to disconnect log streaming — application continues running)"
echo ""

exec ssh -t "${REMOTE}" '
  LOGFILE="/var/log/nemo-headunit.log"
  echo "Starting launcher..."
  sudo touch "$LOGFILE" && sudo chmod 666 "$LOGFILE"
  
  # Kill previous instance if running
  pkill -f "/opt/nemo-headunit/main.py" 2>/dev/null || true
  pkill -f "launch_kiosk" 2>/dev/null || true
  
  # Launch launcher wrapper via nohup
  nohup /opt/nemo-headunit/bin/nemo-headunit > "$LOGFILE" 2>&1 &
  LAUNCH_PID=$!
  
  echo "[SSH] NemoHeadUnit launched (PID: $LAUNCH_PID)."
  echo "[SSH] Streaming live logs from $LOGFILE..."
  
  # Stream live output
  tail -n 30 -f "$LOGFILE"
'
