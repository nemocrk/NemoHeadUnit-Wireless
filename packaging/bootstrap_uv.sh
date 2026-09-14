#!/usr/bin/env bash
# bootstrap_uv.sh — Installs uv standalone binary if not present.
#
# Called by postinst before venv package sync with uv.
# Idempotent: exits immediately if uv is already in PATH or in /opt/uv/bin.
#
# Must be executed as root or with write access to /opt/uv.

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

UV_INSTALL_DIR="/opt/uv"
UV_BIN="${UV_INSTALL_DIR}/bin/uv"

# 1. Check if uv is already available in PATH
if command -v uv &>/dev/null; then
    echo -e "${GREEN}[bootstrap_uv] uv already present: $(command -v uv)${NC}"
    exit 0
fi

# 2. Check if uv is in /opt/uv/bin
if [ -x "${UV_BIN}" ]; then
    echo -e "${GREEN}[bootstrap_uv] uv found in ${UV_INSTALL_DIR} — adding to PATH.${NC}"
    export PATH="${UV_INSTALL_DIR}/bin:${PATH}"
    exit 0
fi

# 3. Download and install uv via Astral official installer or fallback binary archive
echo -e "${CYAN}[bootstrap_uv] uv not found — installing in ${UV_INSTALL_DIR}...${NC}"

mkdir -p "${UV_INSTALL_DIR}/bin"

if command -v curl &>/dev/null; then
    export UV_INSTALL_DIR="${UV_INSTALL_DIR}"
    export CARGO_DIST_FORCE_INSTALL_DIR="${UV_INSTALL_DIR}"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="${UV_INSTALL_DIR}" CARGO_DIST_FORCE_INSTALL_DIR="${UV_INSTALL_DIR}" sh || {
        # Fallback direct binary release download
        ARCH="$(uname -m)"
        case "${ARCH}" in
            x86_64) UV_TARGET="x86_64-unknown-linux-gnu" ;;
            aarch64|arm64) UV_TARGET="aarch64-unknown-linux-gnu" ;;
            armv7l) UV_TARGET="armv7-unknown-linux-gnueabihf" ;;
            *) UV_TARGET="x86_64-unknown-linux-gnu" ;;
        esac
        curl -Ls "https://github.com/astral-sh/uv/releases/latest/download/uv-${UV_TARGET}.tar.gz" | tar -xz -C "${UV_INSTALL_DIR}/bin/" --strip-components=1
    }
elif command -v wget &>/dev/null; then
    ARCH="$(uname -m)"
    case "${ARCH}" in
        x86_64) UV_TARGET="x86_64-unknown-linux-gnu" ;;
        aarch64|arm64) UV_TARGET="aarch64-unknown-linux-gnu" ;;
        armv7l) UV_TARGET="armv7-unknown-linux-gnueabihf" ;;
        *) UV_TARGET="x86_64-unknown-linux-gnu" ;;
    esac
    wget -qO- "https://github.com/astral-sh/uv/releases/latest/download/uv-${UV_TARGET}.tar.gz" | tar -xz -C "${UV_INSTALL_DIR}/bin/" --strip-components=1
else
    echo -e "${RED}[bootstrap_uv] ERROR: neither curl nor wget available. Please install one of them.${NC}" >&2
    exit 1
fi

chmod +x "${UV_INSTALL_DIR}/bin/uv"* 2>/dev/null || true
export PATH="${UV_INSTALL_DIR}/bin:${PATH}"

echo -e "${GREEN}[bootstrap_uv] uv installed in ${UV_INSTALL_DIR}/bin${NC}"
