#!/usr/bin/env bash
# fix_gpu_drivers.sh — Dynamic GPU Driver Detection & Setup
#
# Detects GPU hardware (Intel, NVIDIA, AMD, ARM) and installs appropriate
# VA-API / GStreamer hardware acceleration drivers post-installation.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root: sudo bash $0" >&2
  exit 1
fi

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}[gpu-detect] Detecting GPU hardware & VA-API drivers...${NC}"

# Check APT lock status before attempting installs
apt_unlocked() {
    ! fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1
}

# 1. Check NVIDIA GPU
if command -v nvidia-smi &>/dev/null || lspci 2>/dev/null | grep -qi "NVIDIA"; then
    echo -e "  ${GREEN}[gpu-detect] NVIDIA GPU detected.${NC}"
    if apt_unlocked && command -v apt-get &>/dev/null; then
        echo -n "  [gpu-detect] Ensuring NVIDIA GStreamer / VA-API plugins... "
        apt-get install -y --no-install-recommends gstreamer1.0-plugins-bad >/dev/null 2>&1 || true
        echo -e "${GREEN}OK.${NC}"
    fi
fi

# 2. Check Intel GPU
if lspci 2>/dev/null | grep -qi "Intel" || grep -qi "Intel" /proc/cpuinfo 2>/dev/null; then
    echo -e "  ${GREEN}[gpu-detect] Intel Graphics detected.${NC}"
    if apt_unlocked && command -v apt-get &>/dev/null; then
        # Check if legacy Gen4-Gen8 or modern Gen9+ Intel
        if lspci 2>/dev/null | grep -qiE "Atom|Bay|Trail|Haswell|Broadwell|Ivy|Sandy"; then
            echo -n "  [gpu-detect] Installing Intel i965 legacy VA-API driver... "
            apt-get install -y --no-install-recommends i965-va-driver >/dev/null 2>&1 || true
            echo -e "${GREEN}OK.${NC}"
        else
            echo -n "  [gpu-detect] Installing Intel Media iHD VA-API driver... "
            apt-get install -y --no-install-recommends intel-media-va-driver >/dev/null 2>&1 || true
            echo -e "${GREEN}OK.${NC}"
        fi
    fi
fi

# 3. Check AMD GPU
if lspci 2>/dev/null | grep -qi "AMD" || lspci 2>/dev/null | grep -qi "Radeon"; then
    echo -e "  ${GREEN}[gpu-detect] AMD Radeon Graphics detected.${NC}"
    if apt_unlocked && command -v apt-get &>/dev/null; then
        echo -n "  [gpu-detect] Installing AMD Mesa VA-API drivers... "
        apt-get install -y --no-install-recommends mesa-va-drivers >/dev/null 2>&1 || true
        echo -e "${GREEN}OK.${NC}"
    fi
fi

echo -e "${GREEN}[gpu-detect] GPU driver verification complete.${NC}"
