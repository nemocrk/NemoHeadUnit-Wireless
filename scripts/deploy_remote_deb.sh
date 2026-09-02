#!/usr/bin/env bash
# deploy_remote_deb.sh — Builds, transfers, installs, and launches NemoHeadUnit on a remote host (Arch Linux or Debian/Ubuntu).
# Supports both --method venv (default, with uv) and --method micromamba.
#
# Usage:
#   bash scripts/deploy_remote_deb.sh [--method venv|micromamba] [--arch amd64|arm64|x86_64|aarch64] <user> <host>
#
# Example:
#   bash scripts/deploy_remote_deb.sh nemo 192.168.1.38
#   bash scripts/deploy_remote_deb.sh --method venv nemo 192.168.1.38
#

set -euo pipefail

CLI_ARCH=""
CLI_METHOD="venv"
REMOTE_USER=""
REMOTE_HOST=""

show_help() {
  echo "Usage: $0 [--method venv|micromamba] [--arch amd64|arm64|x86_64|aarch64] <user> <host>"
  echo ""
  echo "Options:"
  echo "  --method     Packaging method: 'venv' (uv-based, default) or 'micromamba' (conda-based)"
  echo "  --venv       Shortcut for --method venv"
  echo "  --micromamba Shortcut for --method micromamba"
  echo "  --arch       Target architecture (e.g. x86_64, aarch64, amd64, arm64. Default: auto-probe)"
  echo "  --help       Show this help message"
  echo ""
  echo "Example:"
  echo "  $0 nemo 192.168.1.38"
  echo "  $0 --method venv nemo 192.168.1.38"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --method)
      CLI_METHOD="$2"
      shift 2
      ;;
    --venv)
      CLI_METHOD="venv"
      shift 1
      ;;
    --micromamba|--conda)
      CLI_METHOD="micromamba"
      shift 1
      ;;
    --arch)
      CLI_ARCH="$2"
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
echo " Deploying NemoHeadUnit over SSH"
echo " Target : $REMOTE"
echo " Method : $CLI_METHOD"
echo " Date   : $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================================="
echo ""

# ---------------------------------------------------------------------------
# Step 1: Ensure SSH autologin (public key authentication)
# ---------------------------------------------------------------------------
echo "[1/5] Checking passwordless SSH autologin to ${REMOTE}..."
if ssh -o BatchMode=yes -o ConnectTimeout=4 -o StrictHostKeyChecking=accept-new "${REMOTE}" "true" 2>/dev/null; then
  echo "[OK] Passwordless SSH already working."
else
  echo "Passwordless SSH not configured. Setting up automatic SSH key authentication..."
  
  PUB_KEY=""
  for key_candidate in "$HOME/.ssh/id_ed25519.pub" "$HOME/.ssh/id_rsa.pub" "$HOME/.ssh/id_ecdsa.pub"; do
    if [ -f "$key_candidate" ]; then
      PUB_KEY="$key_candidate"
      break
    fi
  done

  if [ -z "$PUB_KEY" ]; then
    echo "Generating new local SSH key (~/.ssh/id_ed25519)..."
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519" -q
    PUB_KEY="$HOME/.ssh/id_ed25519.pub"
  fi

  echo "Installing public key (${PUB_KEY}) on ${REMOTE}..."
  if command -v ssh-copy-id &>/dev/null; then
    ssh-copy-id -o StrictHostKeyChecking=accept-new -i "$PUB_KEY" "${REMOTE}" || true
  else
    cat "$PUB_KEY" | ssh -o StrictHostKeyChecking=accept-new "${REMOTE}" "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys" || true
  fi

  if ssh -o BatchMode=yes -o ConnectTimeout=4 "${REMOTE}" "true" 2>/dev/null; then
    echo "[OK] Passwordless SSH autologin configured successfully!"
  else
    echo "Warning: Passwordless SSH check failed. Will continue with standard SSH prompts."
  fi
fi
echo ""

# ---------------------------------------------------------------------------
# Step 2: Probe remote environment (OS distro family & architecture)
# ---------------------------------------------------------------------------
echo "[2/5] Probing remote environment (${REMOTE})..."

REMOTE_OS_DATA=$(ssh "${REMOTE}" '
  if [ -f /etc/os-release ]; then
    cat /etc/os-release
  elif [ -f /usr/lib/os-release ]; then
    cat /usr/lib/os-release
  fi
  echo "---UNAME_M---"
  uname -m
' 2>/dev/null)

REMOTE_UNAME_M=$(echo "$REMOTE_OS_DATA" | awk '/---UNAME_M---/{flag=1;next} flag{print $1}' | head -n 1)
OS_RELEASE_INFO=$(echo "$REMOTE_OS_DATA" | awk '/---UNAME_M---/{exit} {print}')

DETECTED_ID=$(echo "$OS_RELEASE_INFO" | grep -E '^ID=' | head -n 1 | cut -d= -f2 | tr -d '"' | tr '[:upper:]' '[:lower:]' || echo "")
DETECTED_LIKE=$(echo "$OS_RELEASE_INFO" | grep -E '^ID_LIKE=' | head -n 1 | cut -d= -f2 | tr -d '"' | tr '[:upper:]' '[:lower:]' || echo "")
PRETTY_NAME=$(echo "$OS_RELEASE_INFO" | grep -E '^PRETTY_NAME=' | head -n 1 | cut -d= -f2 | tr -d '"' || echo "Linux")

echo "  Remote OS   : ${PRETTY_NAME} (ID: ${DETECTED_ID:-unknown}, LIKE: ${DETECTED_LIKE:-none})"
echo "  Remote Arch : ${REMOTE_UNAME_M:-unknown}"

if [[ "$DETECTED_ID" =~ (arch|manjaro|endeavouros|artix|garuda) ]] || [[ "$DETECTED_LIKE" =~ arch ]]; then
  DISTRO_FAMILY="arch"
elif [[ "$DETECTED_ID" =~ (debian|ubuntu|raspbian|linuxmint|pop|kali) ]] || [[ "$DETECTED_LIKE" =~ (debian|ubuntu) ]]; then
  DISTRO_FAMILY="debian"
else
  if ssh "${REMOTE}" "command -v pacman &>/dev/null"; then
    DISTRO_FAMILY="arch"
  elif ssh "${REMOTE}" "command -v apt-get &>/dev/null"; then
    DISTRO_FAMILY="debian"
  else
    echo "Error: Could not determine package manager on remote host (neither pacman nor apt-get found)." >&2
    exit 1
  fi
fi

if [ -n "$CLI_ARCH" ]; then
  case "$CLI_ARCH" in
    amd64|x86_64)
      ARCH_ARCH="x86_64"
      DEB_ARCH="amd64"
      ;;
    arm64|aarch64)
      ARCH_ARCH="aarch64"
      DEB_ARCH="arm64"
      ;;
    *)
      ARCH_ARCH="$CLI_ARCH"
      DEB_ARCH="$CLI_ARCH"
      ;;
  esac
else
  if [[ "$REMOTE_UNAME_M" =~ (aarch64|arm64|armv8) ]]; then
    ARCH_ARCH="aarch64"
    DEB_ARCH="arm64"
  else
    ARCH_ARCH="x86_64"
    DEB_ARCH="amd64"
  fi
fi

if [ "$DISTRO_FAMILY" = "arch" ]; then
  BUILD_ARCH="$ARCH_ARCH"
else
  BUILD_ARCH="$DEB_ARCH"
fi

echo "  Target Distro : ${DISTRO_FAMILY^^}"
echo "  Target Arch   : ${BUILD_ARCH}"
echo "  Build Method  : ${CLI_METHOD}"
echo ""

# ---------------------------------------------------------------------------
# Step 3: Build package locally & transfer to remote /tmp/
# ---------------------------------------------------------------------------
echo "[3/5] Building ${DISTRO_FAMILY^^} package locally (${BUILD_ARCH}, method=${CLI_METHOD})..."

if [ "$DISTRO_FAMILY" = "arch" ]; then
  bash "${REPO_ROOT}/packaging/build_arch.sh" --method "${CLI_METHOD}" --arch "${BUILD_ARCH}" --output-dir dist
  VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
  PKG_FILENAME="nemo-headunit-${VERSION}-1-${BUILD_ARCH}.pkg.tar.zst"
else
  bash "${REPO_ROOT}/packaging/build_deb.sh" --method "${CLI_METHOD}" --arch "${BUILD_ARCH}" --output-dir dist
  VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION")"
  PKG_FILENAME="nemo-headunit_${VERSION}_${BUILD_ARCH}.deb"
fi

PKG_PATH="${REPO_ROOT}/dist/${PKG_FILENAME}"
if [ ! -f "${PKG_PATH}" ]; then
  echo "Error: Package not found at ${PKG_PATH}" >&2
  exit 1
fi
echo "[OK] Local package build completed: ${PKG_FILENAME}"

echo "Transferring ${PKG_FILENAME} to ${REMOTE}:/tmp/..."
rsync -avz -e ssh "${PKG_PATH}" "${REMOTE}:/tmp/${PKG_FILENAME}"
echo "[OK] Package transferred."
echo ""

# ---------------------------------------------------------------------------
# Step 4: Install package on remote host
# ---------------------------------------------------------------------------
echo "[4/5] Installing package on remote host (${DISTRO_FAMILY^^})..."

if [ "$DISTRO_FAMILY" = "arch" ]; then
  SSH_INSTALL_CMD="sudo pacman -U --noconfirm --needed --overwrite '*' /tmp/${PKG_FILENAME}"
else
  SSH_INSTALL_CMD="if [ -z \"\$(find /var/cache/apt -maxdepth 0 -mmin -1440 2>/dev/null)\" ]; then \
      echo 'Cache vecchia o inesistente. Aggiorno APT...'; sudo apt-get update; \
  else \
      echo 'Cache APT recente. Salto l update.'; \
  fi && sudo apt-get install -f -y /tmp/${PKG_FILENAME}"
fi

ssh -t "${REMOTE}" "$SSH_INSTALL_CMD"
echo "[OK] Remote package installation finished."
echo ""

# ---------------------------------------------------------------------------
# Step 5: Launch application (Qt6 Native GUI default) and stream logs
# ---------------------------------------------------------------------------
echo "[5/5] Starting NemoHeadUnit (Qt6 GUI default) and streaming live output..."
echo "      (Press Ctrl+C to disconnect log streaming — application continues running)"
echo ""

exec ssh -t "${REMOTE}" '
  LOGFILE="/tmp/nemo-headunit.log"
  echo "Starting NemoHeadUnit launcher..."
  touch "$LOGFILE" && chmod 666 "$LOGFILE"
  
  # Kill previous instance if running
  pkill -f "/opt/nemo-headunit/main.py" 2>/dev/null || true
  pkill -f "launch_kiosk" 2>/dev/null || true
  pkill -f "nemo-headunit" 2>/dev/null || true
  
  # Ensure Display / Wayland environment variables are available
  export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  export DISPLAY="${DISPLAY:-:0}"
  if [ -e "${XDG_RUNTIME_DIR}/wayland-0" ]; then
    export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
    export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland}"
  elif [ -e "${XDG_RUNTIME_DIR}/wayland-1" ]; then
    export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"
    export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-wayland}"
  fi

  # Launch launcher wrapper via nohup (Qt6 GUI runs as default)
  nohup systemd-cat -t nemo-headunit /opt/nemo-headunit/bin/nemo-headunit > "$LOGFILE" 2>&1 &
  LAUNCH_PID=$!
  
  echo "[SSH] NemoHeadUnit launched (PID: $LAUNCH_PID)."
  echo "[SSH] Streaming live logs from $LOGFILE..."
  
  # Stream live output
  tail -n 40 -f "$LOGFILE"
'
