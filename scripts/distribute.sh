#!/usr/bin/env bash
# scripts/distribute.sh — Universal NemoHeadUnit Distribution & Deployment Script
#
# Intelligently inspects the target system (Local or Remote SSH), detects OS distro,
# CPU architecture, GPU hardware, and compositor environment, and deploys NemoHeadUnit
# with optimal dependencies, environment bootstrap, hardware quirks, and services.
# Supports bidirectional cross-platform deployment (Linux -> Linux, Linux -> Windows).
#
# Usage:
#   bash scripts/distribute.sh [--local]
#   bash scripts/distribute.sh [--method venv|micromamba|auto] [user@]host
#   bash scripts/distribute.sh --target user@192.168.1.50 --method micromamba
#   bash scripts/distribute.sh nemo@192.168.1.38 --skip-deps --restart
#
# Examples:
#   ./distribute.sh --local                     # Deploy/configure on the current machine
#   ./distribute.sh nemo@192.168.1.38          # Distribute over SSH to a remote target
#   ./distribute.sh --dry-run nemo@192.168.1.38 # Probe target hardware without touching files
#   ./distribute.sh --target win-car --dest C:\\Nemo # Deploy to Windows target over SSH
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

TARGET=""
IS_LOCAL=0
DRY_RUN=0
CLI_METHOD="auto"
TARGET_DIR="/opt/nemo-headunit"
CUSTOM_DEST=0

SKIP_DEPS=0
SKIP_SERVICE=0
SKIP_HW_FIXES=0
RESTART_AFTER=0
CLEAN_TARGET=0
FORCE_BUILD=0
REBOOT_AFTER=0
SSH_PORT=""
SSH_IDENTITY=""

# Terminal Colors
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

show_help() {
  echo -e "${BOLD}NemoHeadUnit Universal Distribution Tool (Bash)${NC}"
  echo "Usage: $0 [options] [[user@]host]"
  echo ""
  echo "Options:"
  echo "  --local                  Distribute and configure directly on the local system"
  echo "  --target, -t <host>      Specify remote target in format [user@]hostname or IP"
  echo "  --method, -m <engine>    Environment engine: 'auto' (default), 'micromamba', or 'venv'"
  echo "  --dest, -d <path>        Target directory (default: /opt/nemo-headunit on Linux, C:\\NemoHeadUnit-Wireless on Windows)"
  echo "  --dry-run, -n            Inspect & probe target without modifying files or installing packages"
  echo "  --skip-deps              Skip system package and Python dependency installations (fast code sync)"
  echo "  --skip-hardware-fixes    Skip execution of hardware adaptation and quirks scripts"
  echo "  --skip-service           Skip installation of systemd unit or desktop shortcuts"
  echo "  --restart, -r, --start   Automatically restart/start NemoHeadUnit service after deployment"
  echo "  --reboot                 Reboot target system after deployment"
  echo "  --clean, -c              Clean destination directory / pycache before deployment"
  echo "  --build, -b              Force rebuild of package (deb/.pkg.tar.zst) before distribution"
  echo "  --port, -p <port>        Custom SSH port for remote target"
  echo "  --identity, -i <key>     Custom SSH private key file"
  echo "  --help, -h               Show this help message"
  echo ""
  echo "Examples:"
  echo "  $0 --local"
  echo "  $0 nemo@192.168.1.38 --skip-deps --restart"
  echo "  $0 --target root@baytrail-tablet --method micromamba"
  echo "  $0 --dry-run Administrator@192.168.1.50"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local)
      IS_LOCAL=1
      shift
      ;;
    --target|-t)
      TARGET="$2"
      shift 2
      ;;
    --method|-m)
      CLI_METHOD="$2"
      shift 2
      ;;
    --dry-run|-n)
      DRY_RUN=1
      shift
      ;;
    --dest|-d)
      TARGET_DIR="$2"
      CUSTOM_DEST=1
      shift 2
      ;;
    --skip-deps)
      SKIP_DEPS=1
      shift
      ;;
    --skip-hardware-fixes)
      SKIP_HW_FIXES=1
      shift
      ;;
    --skip-service)
      SKIP_SERVICE=1
      shift
      ;;
    --restart|-r|--start)
      RESTART_AFTER=1
      shift
      ;;
    --reboot)
      REBOOT_AFTER=1
      shift
      ;;
    --clean|-c)
      CLEAN_TARGET=1
      shift
      ;;
    --build|-b)
      FORCE_BUILD=1
      shift
      ;;
    --port|-p)
      SSH_PORT="$2"
      shift 2
      ;;
    --identity|-i)
      SSH_IDENTITY="$2"
      shift 2
      ;;
    --help|-h)
      show_help
      ;;
    *)
      if [ -z "$TARGET" ]; then
        TARGET="$1"
      else
        echo -e "${RED}Unknown argument: $1${NC}"
        show_help
      fi
      shift
      ;;
  esac
done

if [ "$TARGET_DIR" = "." ] || [ "$TARGET_DIR" = "current" ]; then
  TARGET_DIR="$REPO_ROOT"
fi

if [ -z "$TARGET" ] && [ $IS_LOCAL -eq 0 ]; then
  # Default to local if no target supplied
  echo -e "${YELLOW}No remote target specified. Defaulting to local system deployment (--local).${NC}"
  IS_LOCAL=1
fi

# Detect if executing on local Windows host from Bash (Git Bash / MSYS2 / Cygwin)
HOST_UNAME="$(uname -s 2>/dev/null || true)"
if [ $IS_LOCAL -eq 1 ] && [[ "$HOST_UNAME" =~ (MINGW|MSYS|CYGWIN) ]]; then
  echo -e "${YELLOW}Detected execution on local Windows host from Bash environment (${HOST_UNAME}).${NC}"
  echo -e "Delegating execution to native PowerShell distributor (scripts/distribute.ps1)...\n"
  PS_DELEGATE_ARGS=("-Local" "-Method" "${CLI_METHOD}")
  [ $CUSTOM_DEST -eq 1 ] && PS_DELEGATE_ARGS+=("-Dest" "${TARGET_DIR}")
  [ $DRY_RUN -eq 1 ] && PS_DELEGATE_ARGS+=("-DryRun")
  [ $SKIP_DEPS -eq 1 ] && PS_DELEGATE_ARGS+=("-SkipDeps")
  [ $SKIP_SERVICE -eq 1 ] && PS_DELEGATE_ARGS+=("-SkipService")
  [ $SKIP_HW_FIXES -eq 1 ] && PS_DELEGATE_ARGS+=("-SkipHardwareFixes")
  [ $RESTART_AFTER -eq 1 ] && PS_DELEGATE_ARGS+=("-Restart")
  [ $CLEAN_TARGET -eq 1 ] && PS_DELEGATE_ARGS+=("-Clean")
  exec powershell.exe -NoProfile -ExecutionPolicy Bypass -File "${SCRIPT_DIR}/distribute.ps1" "${PS_DELEGATE_ARGS[@]}"
fi

echo -e "${CYAN}${BOLD}==============================================================${NC}"
echo -e "${CYAN}${BOLD} 🚀 NemoHeadUnit Universal System Distributor (Bash)${NC}"
echo -e "${CYAN}${BOLD} Date   : $(date '+%Y-%m-%d %H:%M:%S')${NC}"
echo -e "${CYAN}${BOLD} Target : $([ $IS_LOCAL -eq 1 ] && echo "Local System ($(hostname))" || echo "$TARGET")${NC}"
echo -e "${CYAN}${BOLD} Method : ${CLI_METHOD}${NC}"
echo -e "${CYAN}${BOLD} Mode   : $([ $DRY_RUN -eq 1 ] && echo "DRY-RUN (Probe only)" || echo "Full Deployment")${NC}"
echo -e "${CYAN}${BOLD} Flags  : SkipDeps=${SKIP_DEPS} | SkipService=${SKIP_SERVICE} | SkipFixes=${SKIP_HW_FIXES} | Restart=${RESTART_AFTER}${NC}"
echo -e "${CYAN}${BOLD}==============================================================${NC}\n"

# -----------------------------------------------------------------------------
# Remote SSH Helper Wrapper
# -----------------------------------------------------------------------------
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new)
if [ -n "$SSH_PORT" ]; then
  SSH_OPTS+=(-p "$SSH_PORT")
fi
if [ -n "$SSH_IDENTITY" ]; then
  SSH_OPTS+=(-i "$SSH_IDENTITY")
fi

run_cmd() {
  if [ $IS_LOCAL -eq 1 ]; then
    bash -c "$1"
  else
    ssh "${SSH_OPTS[@]}" "$TARGET" "$1"
  fi
}

# -----------------------------------------------------------------------------
# Step 1: Connectivity Check & Authentication Setup
# -----------------------------------------------------------------------------
if [ $IS_LOCAL -eq 0 ]; then
  echo -e "${BOLD}[1/5] Checking SSH connectivity to ${TARGET}...${NC}"
  if ! ssh "${SSH_OPTS[@]}" "$TARGET" "exit 0" 2>/dev/null; then
    echo -e "${YELLOW}Passwordless SSH not yet configured. Attempting public key setup...${NC}"
    PUB_KEY=""
    for k in "$HOME/.ssh/id_ed25519.pub" "$HOME/.ssh/id_rsa.pub"; do
      if [ -f "$k" ]; then PUB_KEY="$k"; break; fi
    done
    if [ -z "$PUB_KEY" ]; then
      mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
      ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519" -q
      PUB_KEY="$HOME/.ssh/id_ed25519.pub"
    fi
    if command -v ssh-copy-id &>/dev/null; then
      COPY_ID_ARGS=(-o StrictHostKeyChecking=accept-new -i "$PUB_KEY")
      [ -n "$SSH_PORT" ] && COPY_ID_ARGS+=(-p "$SSH_PORT")
      ssh-copy-id "${COPY_ID_ARGS[@]}" "$TARGET" || true
    fi
  fi
  echo -e "  ${GREEN}✓ Connected successfully via SSH.${NC}\n"
else
  echo -e "${BOLD}[1/5] Initializing local distribution pipeline...${NC}\n"
fi

# -----------------------------------------------------------------------------
# Step 2: System & Hardware Inspection
# -----------------------------------------------------------------------------
echo -e "${BOLD}[2/5] Inspecting target system environment and hardware...${NC}"

IS_TARGET_WINDOWS=0
if [ $IS_LOCAL -eq 1 ]; then
  if [[ "$(uname -s)" =~ (MINGW|MSYS|CYGWIN) ]]; then
    IS_TARGET_WINDOWS=1
  fi
else
  # Robust Windows detection: query powershell.exe which exists uniquely on Windows NT systems
  WIN_CHECK=$(ssh "${SSH_OPTS[@]}" "$TARGET" 'powershell.exe -NoProfile -Command "Write-Output IS_WINDOWS"' 2>/dev/null || true)
  if [[ "$WIN_CHECK" == *"IS_WINDOWS"* ]]; then
    IS_TARGET_WINDOWS=1
  fi
fi

if [ $IS_TARGET_WINDOWS -eq 1 ]; then
  # Normalize Windows target path if using Linux default
  if [ $CUSTOM_DEST -eq 0 ] || [ "$TARGET_DIR" = "/opt/nemo-headunit" ]; then
    TARGET_DIR="C:\\NemoHeadUnit-Wireless"
  fi

  WIN_PS_SCRIPT='
    $ProgressPreference = "SilentlyContinue"
    Write-Output "PROBE_RESULT:OS_NAME=Windows"
    Write-Output "PROBE_RESULT:ARCH=$env:PROCESSOR_ARCHITECTURE"
    Write-Output "PROBE_RESULT:DISTRO_ID=windows"
    Write-Output "PROBE_RESULT:DISTRO_LIKE=windows"
    $cap = (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption
    Write-Output "PROBE_RESULT:PRETTY_NAME=$cap"
    Write-Output "PROBE_RESULT:PKG_MGR=winget"
    $model = (Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue).Model
    Write-Output "PROBE_RESULT:DMI_PRODUCT=$model"
    $gpus = ((Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name) -join ", ")
    Write-Output "PROBE_RESULT:GPU_INFO=$gpus"
    Write-Output "PROBE_RESULT:HAS_WAYLAND=no"
    Write-Output "PROBE_RESULT:HAS_X11=no"
    $has_mm = ((Get-Command micromamba -ErrorAction SilentlyContinue) -ne $null)
    Write-Output "PROBE_RESULT:HAS_MICROMAMBA=$has_mm"
    $has_uv = ((Get-Command uv -ErrorAction SilentlyContinue) -ne $null)
    Write-Output "PROBE_RESULT:HAS_UV=$has_uv"
  '
  WIN_B64=$(echo -n "$WIN_PS_SCRIPT" | iconv -f UTF-8 -t UTF-16LE | base64 -w 0)
  PROBE_OUTPUT=$(run_cmd "powershell.exe -NoProfile -EncodedCommand $WIN_B64")
else
  # Linux / Unix Target Probe
  PROBE_SCRIPT='
    OS_NAME=$(uname -s)
    ARCH=$(uname -m)
    
    DISTRO_ID="unknown"
    DISTRO_LIKE=""
    PRETTY_NAME="$OS_NAME"
    if [ -f /etc/os-release ]; then
      . /etc/os-release
      DISTRO_ID="${ID:-unknown}"
      DISTRO_LIKE="${ID_LIKE:-}"
      PRETTY_NAME="${PRETTY_NAME:-$DISTRO_ID}"
    fi

    PKG_MGR="unknown"
    if command -v pacman &>/dev/null; then PKG_MGR="pacman";
    elif command -v apt-get &>/dev/null; then PKG_MGR="apt";
    elif command -v dnf &>/dev/null; then PKG_MGR="dnf";
    elif command -v zypper &>/dev/null; then PKG_MGR="zypper";
    fi

    # Hardware / Board model
    DMI_PRODUCT=""
    if [ -f /sys/class/dmi/id/product_name ]; then
      DMI_PRODUCT=$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)
    elif command -v dmidecode &>/dev/null; then
      DMI_PRODUCT=$(dmidecode -s system-product-name 2>/dev/null || true)
    fi

    # GPU Hardware probe
    GPU_INFO="generic"
    if command -v nvidia-smi &>/dev/null; then
      GPU_INFO="nvidia"
    elif lspci 2>/dev/null | grep -Ei "vga|3d|display" | grep -qi "intel"; then
      GPU_INFO="intel"
    elif lspci 2>/dev/null | grep -Ei "vga|3d|display" | grep -qi "amd|radeon"; then
      GPU_INFO="amd"
    elif [ -d /dev/dri ]; then
      GPU_INFO="dri_generic"
    fi

    # Compositor / Display environment
    HAS_WAYLAND="no"
    [ -n "${WAYLAND_DISPLAY:-}" ] && HAS_WAYLAND="yes"
    HAS_X11="no"
    [ -n "${DISPLAY:-}" ] && HAS_X11="yes"

    # Python runtime probe
    HAS_MICROMAMBA="no"
    command -v micromamba &>/dev/null && HAS_MICROMAMBA="yes"
    [ -x /opt/micromamba/micromamba ] && HAS_MICROMAMBA="yes"

    HAS_UV="no"
    command -v uv &>/dev/null && HAS_UV="yes"
    [ -x /opt/uv/bin/uv ] && HAS_UV="yes"

    echo "PROBE_RESULT:OS_NAME=$OS_NAME"
    echo "PROBE_RESULT:ARCH=$ARCH"
    echo "PROBE_RESULT:DISTRO_ID=$DISTRO_ID"
    echo "PROBE_RESULT:DISTRO_LIKE=$DISTRO_LIKE"
    echo "PROBE_RESULT:PRETTY_NAME=$PRETTY_NAME"
    echo "PROBE_RESULT:PKG_MGR=$PKG_MGR"
    echo "PROBE_RESULT:DMI_PRODUCT=$DMI_PRODUCT"
    echo "PROBE_RESULT:GPU_INFO=$GPU_INFO"
    echo "PROBE_RESULT:HAS_WAYLAND=$HAS_WAYLAND"
    echo "PROBE_RESULT:HAS_X11=$HAS_X11"
    echo "PROBE_RESULT:HAS_MICROMAMBA=$HAS_MICROMAMBA"
    echo "PROBE_RESULT:HAS_UV=$HAS_UV"
  '
  PROBE_OUTPUT=$(run_cmd "$PROBE_SCRIPT")
fi

parse_val() {
  echo "$PROBE_OUTPUT" | grep "PROBE_RESULT:$1=" | cut -d= -f2- | tr -d '\r'
}

TARGET_OS=$(parse_val "OS_NAME")
TARGET_ARCH=$(parse_val "ARCH")
DISTRO_ID=$(parse_val "DISTRO_ID")
DISTRO_LIKE=$(parse_val "DISTRO_LIKE")
PRETTY_NAME=$(parse_val "PRETTY_NAME")
PKG_MGR=$(parse_val "PKG_MGR")
DMI_PRODUCT=$(parse_val "DMI_PRODUCT")
GPU_INFO=$(parse_val "GPU_INFO")
HAS_WAYLAND=$(parse_val "HAS_WAYLAND")
HAS_X11=$(parse_val "HAS_X11")
HAS_MICROMAMBA=$(parse_val "HAS_MICROMAMBA")
HAS_UV=$(parse_val "HAS_UV")

echo -e "  Target OS         : ${GREEN}${PRETTY_NAME:-$TARGET_OS} (${TARGET_OS})${NC}"
echo -e "  Architecture      : ${GREEN}${TARGET_ARCH}${NC}"
echo -e "  Package Manager   : ${GREEN}${PKG_MGR}${NC}"
echo -e "  Hardware / DMI    : ${GREEN}${DMI_PRODUCT:-Standard PC / Embedded Device}${NC}"
echo -e "  GPU Vendor        : ${GREEN}${GPU_INFO}${NC}"
echo -e "  Target Path       : ${GREEN}${TARGET_DIR}${NC}"
echo -e "  Available Engines : Micromamba=${HAS_MICROMAMBA}, UV/Venv=${HAS_UV}\n"

# Resolve distribution strategy
DEPLOY_STRATEGY="directory"
if [ "$IS_TARGET_WINDOWS" -eq 1 ]; then
  DEPLOY_STRATEGY="windows_native"
elif [ "$PKG_MGR" = "apt" ]; then
  DEPLOY_STRATEGY="deb_package"
elif [ "$PKG_MGR" = "pacman" ]; then
  DEPLOY_STRATEGY="arch_pacman"
fi

SELECTED_METHOD="$CLI_METHOD"
if [ "$SELECTED_METHOD" = "auto" ]; then
  if [[ "$HAS_MICROMAMBA" =~ ^(yes|True|true)$ ]] || [ -f "${TARGET_DIR}/env/bin/python" ] || [ -f "${TARGET_DIR}/env/python.exe" ]; then
    SELECTED_METHOD="micromamba"
  else
    SELECTED_METHOD="venv"
  fi
fi
echo -e "  ${CYAN}→ Selected Strategy: ${DEPLOY_STRATEGY} | Python Engine: ${SELECTED_METHOD}${NC}\n"

if [ $DRY_RUN -eq 1 ]; then
  echo -e "${GREEN}${BOLD}✓ [Dry-Run] Target inspection completed successfully. No files copied, no packages modified.${NC}"
  exit 0
fi

# -----------------------------------------------------------------------------
# Step 3: Package Transfer / Synchronization
# -----------------------------------------------------------------------------
echo -e "${BOLD}[3/5] Synchronizing application files to target...${NC}"

if [ $IS_TARGET_WINDOWS -eq 1 ]; then
  # Windows Target: Distribute only necessary runtime folders & configuration files
  WIN_ITEMS=("main.py" "backend" "frontend" "protos" "scripts" "services" "packaging" "environment.windows.yml" "environment.yml" "VERSION")
  SYNC_ITEMS=()
  for item in "${WIN_ITEMS[@]}"; do
    if [ -e "${REPO_ROOT}/${item}" ]; then
      SYNC_ITEMS+=("${item}")
    fi
  done

  echo "  Streaming essential Windows runtime files over SSH to ${TARGET}:${TARGET_DIR}..."
  WIN_INIT_PS="if (-not (Test-Path '${TARGET_DIR}')) { New-Item -ItemType Directory -Path '${TARGET_DIR}' -Force | Out-Null }"
  if [ $CLEAN_TARGET -eq 1 ]; then
    WIN_INIT_PS="${WIN_INIT_PS}; Remove-Item -Path '${TARGET_DIR}\\*' -Recurse -Force -Exclude '.git' -ErrorAction SilentlyContinue"
  fi
  ssh "${SSH_OPTS[@]}" "$TARGET" "powershell.exe -NoProfile -Command \"${WIN_INIT_PS}\""
  tar -cz -C "$REPO_ROOT" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    "${SYNC_ITEMS[@]}" | ssh "${SSH_OPTS[@]}" "$TARGET" "tar.exe -xz -C \"${TARGET_DIR}\""
  echo -e "  ${GREEN}✓ Windows runtime synchronization complete.${NC}\n"

elif [ "$DEPLOY_STRATEGY" = "deb_package" ]; then
  # Linux Debian/Ubuntu Target: Distribute .deb package
  DEB_ARCH="amd64"
  if [[ "$TARGET_ARCH" =~ (aarch64|arm64) ]]; then
    DEB_ARCH="arm64"
  fi
  CURRENT_VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION" 2>/dev/null || true)"
  LATEST_PKG=$(ls -t "${REPO_ROOT}/dist/"*"${CURRENT_VERSION}"*"_${DEB_ARCH}".deb 2>/dev/null | head -n 1 || true)
  PKG_MATCHES_METHOD=1
  if [ -n "$LATEST_PKG" ] && [ -f "$LATEST_PKG" ]; then
    if [ "$SELECTED_METHOD" = "venv" ] && ! dpkg-deb -c "$LATEST_PKG" 2>/dev/null | grep -q "requirements.txt"; then
      PKG_MATCHES_METHOD=0
    elif [ "$SELECTED_METHOD" = "micromamba" ] && ! dpkg-deb -c "$LATEST_PKG" 2>/dev/null | grep -q "environment.yml"; then
      PKG_MATCHES_METHOD=0
    fi
  fi
  if [ -z "$LATEST_PKG" ] || [ $FORCE_BUILD -eq 1 ] || [ $PKG_MATCHES_METHOD -eq 0 ]; then
    echo "  Package matching v${CURRENT_VERSION} (${DEB_ARCH}, method=${SELECTED_METHOD}) not found in dist/ or rebuild needed. Building .deb package..."
    bash "${REPO_ROOT}/packaging/build_deb.sh" --method "${SELECTED_METHOD}" --arch "${DEB_ARCH}" --output-dir "dist"
    LATEST_PKG=$(ls -t "${REPO_ROOT}/dist/"*_"${DEB_ARCH}".deb 2>/dev/null | head -n 1 || true)
  fi
  [ -n "$LATEST_PKG" ] && [ -f "$LATEST_PKG" ] || { echo -e "${RED}Failed to find or build .deb package!${NC}"; exit 1; }
  PKG_NAME="$(basename "${LATEST_PKG}")"
  echo -e "  Using package: ${GREEN}${PKG_NAME}${NC}"

  if [ $IS_LOCAL -eq 1 ]; then
    echo "  Installing ${PKG_NAME} on local system via APT..."
    sudo apt-get update -qq || true
    sudo apt-get install --reinstall -y "${LATEST_PKG}" || (sudo dpkg -i "${LATEST_PKG}" && sudo apt-get install -f -y)
  else
    echo "  Transferring ${PKG_NAME} to remote target (${TARGET}:/tmp/)..."
    ssh "${SSH_OPTS[@]}" "$TARGET" "cat > '/tmp/${PKG_NAME}'" < "${LATEST_PKG}"
    echo "  Installing ${PKG_NAME} on remote target via APT..."
    ssh "${SSH_OPTS[@]}" "$TARGET" "sudo apt-get update -qq || true; sudo apt-get install --reinstall -y '/tmp/${PKG_NAME}' || (sudo dpkg -i '/tmp/${PKG_NAME}' && sudo apt-get install -f -y); rm -f '/tmp/${PKG_NAME}'"
  fi
  echo -e "  ${GREEN}✓ .deb package distribution complete.${NC}\n"

elif [ "$DEPLOY_STRATEGY" = "arch_pacman" ]; then
  # Linux Arch Target: Distribute .pkg.tar.zst package
  ARCH_PKG_ARCH="x86_64"
  if [[ "$TARGET_ARCH" =~ (aarch64|arm64) ]]; then
    ARCH_PKG_ARCH="aarch64"
  fi
  CURRENT_VERSION="$(tr -d '[:space:]' < "${REPO_ROOT}/VERSION" 2>/dev/null || true)"
  LATEST_PKG=$(ls -t "${REPO_ROOT}/dist/"*"${CURRENT_VERSION}"*"-${ARCH_PKG_ARCH}".pkg.tar.zst 2>/dev/null | head -n 1 || true)
  PKG_MATCHES_METHOD=1
  if [ -n "$LATEST_PKG" ] && [ -f "$LATEST_PKG" ]; then
    if [ "$SELECTED_METHOD" = "venv" ] && ! tar -tf "$LATEST_PKG" 2>/dev/null | grep -q "requirements.txt"; then
      PKG_MATCHES_METHOD=0
    elif [ "$SELECTED_METHOD" = "micromamba" ] && ! tar -tf "$LATEST_PKG" 2>/dev/null | grep -q "environment.yml"; then
      PKG_MATCHES_METHOD=0
    fi
  fi
  if [ -z "$LATEST_PKG" ] || [ $FORCE_BUILD -eq 1 ] || [ $PKG_MATCHES_METHOD -eq 0 ]; then
    echo "  Package matching v${CURRENT_VERSION} (${ARCH_PKG_ARCH}, method=${SELECTED_METHOD}) not found in dist/ or rebuild needed. Building .pkg.tar.zst package..."
    bash "${REPO_ROOT}/packaging/build_arch.sh" --method "${SELECTED_METHOD}" --arch "${ARCH_PKG_ARCH}" --output-dir "dist"
    LATEST_PKG=$(ls -t "${REPO_ROOT}/dist/"*-"${ARCH_PKG_ARCH}".pkg.tar.zst 2>/dev/null | head -n 1 || true)
  fi
  [ -n "$LATEST_PKG" ] && [ -f "$LATEST_PKG" ] || { echo -e "${RED}Failed to find or build .pkg.tar.zst package!${NC}"; exit 1; }
  PKG_NAME="$(basename "${LATEST_PKG}")"
  echo -e "  Using package: ${GREEN}${PKG_NAME}${NC}"

  if [ $IS_LOCAL -eq 1 ]; then
    echo "  Installing ${PKG_NAME} on local system via Pacman..."
    sudo pacman -U --noconfirm --overwrite '*' "${LATEST_PKG}"
  else
    echo "  Transferring ${PKG_NAME} to remote target (${TARGET}:/tmp/)..."
    ssh "${SSH_OPTS[@]}" "$TARGET" "cat > '/tmp/${PKG_NAME}'" < "${LATEST_PKG}"
    echo "  Installing ${PKG_NAME} on remote target via Pacman..."
    ssh "${SSH_OPTS[@]}" "$TARGET" "sudo pacman -U --noconfirm --overwrite '*' '/tmp/${PKG_NAME}' && rm -f '/tmp/${PKG_NAME}'"
  fi
  echo -e "  ${GREEN}✓ .pkg.tar.zst package distribution complete.${NC}\n"

else
  # Generic Linux Directory Fallback
  echo "  Syncing repository payload to ${TARGET_DIR}..."
  if [ $IS_LOCAL -eq 1 ]; then
    sudo mkdir -p "${TARGET_DIR}"
    sudo rsync -a --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' --exclude='env' "${REPO_ROOT}/" "${TARGET_DIR}/"
  else
    ssh "${SSH_OPTS[@]}" "$TARGET" "sudo mkdir -p ${TARGET_DIR} && sudo chown -R \$(id -un):\$(id -gn) ${TARGET_DIR}"
    tar -cz -C "$REPO_ROOT" --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' --exclude='env' --exclude='build' --exclude='dist' . | ssh "${SSH_OPTS[@]}" "$TARGET" "tar -xz -C ${TARGET_DIR}"
  fi
  echo -e "  ${GREEN}✓ Synchronization complete.${NC}\n"
fi

# -----------------------------------------------------------------------------
# Step 4: Environment Bootstrap & System Dependency Installation
# -----------------------------------------------------------------------------
echo -e "${BOLD}[4/5] Configuring target environment and post-install hooks...${NC}"

if [ $IS_TARGET_WINDOWS -eq 1 ]; then
  echo "  Invoking Windows PowerShell distributor on target host with forwarded flags..."
  REMOTE_PS_ARGS="-Local -Dest \"${TARGET_DIR}\" -Method ${SELECTED_METHOD}"
  [ $SKIP_DEPS -eq 1 ] && REMOTE_PS_ARGS="$REMOTE_PS_ARGS -SkipDeps"
  [ $SKIP_SERVICE -eq 1 ] && REMOTE_PS_ARGS="$REMOTE_PS_ARGS -SkipService"
  [ $SKIP_HW_FIXES -eq 1 ] && REMOTE_PS_ARGS="$REMOTE_PS_ARGS -SkipHardwareFixes"
  [ $RESTART_AFTER -eq 1 ] && REMOTE_PS_ARGS="$REMOTE_PS_ARGS -Restart"
  [ $CLEAN_TARGET -eq 1 ] && REMOTE_PS_ARGS="$REMOTE_PS_ARGS -Clean"
  
  ssh "${SSH_OPTS[@]}" "$TARGET" "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"${TARGET_DIR}\\scripts\\distribute.ps1\" ${REMOTE_PS_ARGS}"
else
  # Linux Post-Install / Hooks
  if [ ${SKIP_HW_FIXES} -eq 0 ]; then
    echo "  Probing and running hardware adaptation scripts..."
    if [ $IS_LOCAL -eq 1 ]; then
      if [ -f /opt/nemo-headunit/hardware_fixes/run_hardware_fixes.sh ]; then
        sudo bash /opt/nemo-headunit/hardware_fixes/run_hardware_fixes.sh || true
      elif [ -f "${TARGET_DIR}/packaging/hardware_fixes/run_hardware_fixes.sh" ]; then
        sudo bash "${TARGET_DIR}/packaging/hardware_fixes/run_hardware_fixes.sh" || true
      fi
    else
      ssh "${SSH_OPTS[@]}" "$TARGET" "[ -f /opt/nemo-headunit/hardware_fixes/run_hardware_fixes.sh ] && sudo bash /opt/nemo-headunit/hardware_fixes/run_hardware_fixes.sh || true"
    fi
  else
    echo "  [SkipHardwareFixes] Bypassing hardware adaptation scripts."
  fi

  if [ ${RESTART_AFTER} -eq 1 ]; then
    echo "  [Restart] Restarting nemo-kiosk service..."
    if [ $IS_LOCAL -eq 1 ]; then
      sudo systemctl daemon-reload || true
      sudo systemctl restart nemo-kiosk.service 2>/dev/null || sudo systemctl start nemo-kiosk.service 2>/dev/null || true
    else
      ssh "${SSH_OPTS[@]}" "$TARGET" "sudo systemctl daemon-reload || true; sudo systemctl restart nemo-kiosk.service 2>/dev/null || sudo systemctl start nemo-kiosk.service 2>/dev/null || true"
    fi
  fi
fi
echo -e "  ${GREEN}✓ Environment configuration complete.${NC}\n"

# -----------------------------------------------------------------------------
# Step 5: Verification & Diagnostics
# -----------------------------------------------------------------------------
echo -e "${BOLD}[5/5] Running deployment diagnostics...${NC}"

if [ $IS_TARGET_WINDOWS -eq 1 ]; then
  ssh "${SSH_OPTS[@]}" "$TARGET" "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command \"if (Get-Command micromamba -ErrorAction SilentlyContinue) { micromamba run -n NemoHeadUnit-Wireless python '${TARGET_DIR}\\scripts\\hardware_tests\\verify_windows_qt6.py' } elseif (Get-Command python -ErrorAction SilentlyContinue) { python '${TARGET_DIR}\\scripts\\hardware_tests\\verify_windows_qt6.py' }\"" || true
else
  DIAG_COMMANDS=$(cat <<EOF
    cd "${TARGET_DIR}"
    if [ -f scripts/hardware_tests/test_hwaccel_diag.sh ]; then
      bash scripts/hardware_tests/test_hwaccel_diag.sh | grep -E "(Optimal Decoder|GPU detected|Hardware)" || true
    fi
EOF
  )
  run_cmd "$DIAG_COMMANDS" || true
fi

echo -e "\n${GREEN}${BOLD}==============================================================${NC}"
echo -e "${GREEN}${BOLD} 🎉 Distribution & Deployment Finished Successfully!${NC}"
echo -e "${GREEN}${BOLD}==============================================================${NC}"
echo -e "Target is ready. Status:"
if [ $RESTART_AFTER -eq 1 ]; then
  echo -e "  • Service restarted/active on target!"
fi
if [ $IS_LOCAL -eq 1 ]; then
  echo -e "  • Start Backend Orchestrator: micromamba run -n NemoHeadUnit-Wireless python backend/main.py"
  echo -e "  • Or Launch via Systemd:      sudo systemctl start nemo-kiosk.service"
  echo -e "  • Or Launch Standalone:       nemo-headunit\n"
elif [ $IS_TARGET_WINDOWS -eq 1 ]; then
  echo -e "  • Launch on Windows Host:     ssh ${TARGET} 'powershell -NoProfile -Command \"& \"${TARGET_DIR}\\scripts\\launch_qt_kiosk.bat\"\"'"
  echo -e "  • Or Launch Desktop Shortcut: NemoHeadUnit.lnk"
  echo -e "  • Connect to Web UI:          http://${TARGET#*@}:8000\n"
else
  echo -e "  • Launch via SSH:             ssh ${TARGET} 'sudo systemctl start nemo-kiosk.service'"
  echo -e "  • Connect to Web UI:          http://${TARGET#*@}:8000\n"
fi

if [ ${REBOOT_AFTER} -eq 1 ]; then
  echo -e "${YELLOW}${BOLD}🔄 [Reboot] Rebooting target system as requested...${NC}"
  if [ $IS_LOCAL -eq 1 ]; then
    sudo reboot
  else
    ssh "${SSH_OPTS[@]}" "$TARGET" "sudo reboot" || true
  fi
fi
