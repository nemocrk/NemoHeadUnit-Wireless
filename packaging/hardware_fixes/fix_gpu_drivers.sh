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
            echo -n "  [gpu-detect] Installing Intel i965 legacy VA-API driver (apt)... "
            apt-get install -y --no-install-recommends i965-va-driver >/dev/null 2>&1 || true
            echo -e "${GREEN}OK.${NC}"
        else
            echo -n "  [gpu-detect] Installing Intel Media iHD VA-API driver (apt)... "
            apt-get install -y --no-install-recommends intel-media-va-driver >/dev/null 2>&1 || true
            echo -e "${GREEN}OK.${NC}"
        fi
    elif command -v pacman &>/dev/null; then
        echo -n "  [gpu-detect] Installing Intel VA-API drivers (pacman)... "
        pacman -S --noconfirm --needed libva-intel-driver intel-media-driver intel-gpu-tools gstreamer-vaapi gst-plugin-va >/dev/null 2>&1 || true
        echo -e "${GREEN}OK.${NC}"
    fi
fi

# 3. Check AMD GPU
if lspci 2>/dev/null | grep -qi "AMD" || lspci 2>/dev/null | grep -qi "Radeon"; then
    echo -e "  ${GREEN}[gpu-detect] AMD Radeon Graphics detected.${NC}"
    if apt_unlocked && command -v apt-get &>/dev/null; then
        echo -n "  [gpu-detect] Installing AMD Mesa VA-API drivers (apt)... "
        apt-get install -y --no-install-recommends mesa-va-drivers >/dev/null 2>&1 || true
        echo -e "${GREEN}OK.${NC}"
    elif command -v pacman &>/dev/null; then
        echo -n "  [gpu-detect] Installing AMD Mesa VA-API drivers (pacman)... "
        pacman -S --noconfirm --needed mesa-vdpau libva-mesa-driver >/dev/null 2>&1 || true
        echo -e "${GREEN}OK.${NC}"
    fi
fi

# 4. Configure DRI permissions and user groups for hardware GPU acceleration
echo -n "  [gpu-detect] Configuring DRI permissions & video/render groups... "
mkdir -p /etc/udev/rules.d
cat <<'EOF' > /etc/udev/rules.d/99-nemo-dri.rules
# Allow non-root users full access to DRI / GPU nodes for VA-API and Wayland EGL
KERNEL=="card*", SUBSYSTEM=="drm", GROUP="video", MODE="0666"
KERNEL=="renderD*", SUBSYSTEM=="drm", GROUP="render", MODE="0666"
EOF
if command -v udevadm &>/dev/null; then
    udevadm control --reload-rules >/dev/null 2>&1 || true
    udevadm trigger --subsystem-match=drm >/dev/null 2>&1 || true
fi
for home_dir in /home/*; do
    if [ -d "$home_dir" ]; then
        user_name="$(basename "$home_dir")"
        if id "$user_name" &>/dev/null; then
            usermod -aG video,render "$user_name" 2>/dev/null || true
        fi
    fi
done
echo -e "${GREEN}OK.${NC}"

# 5. Fix AppArmor permissions for surf kiosk browser if AppArmor is active
if [ -d "/etc/apparmor.d" ] && [ -f "/etc/apparmor.d/usr.bin.surf" ]; then
    echo -n "  [gpu-detect] Configuring AppArmor permissions for /usr/bin/surf... "
    mkdir -p /etc/apparmor.d/local
    cat <<'EOF' > /etc/apparmor.d/local/usr.bin.surf
# Allow surf kiosk browser & GStreamer child processes to access DRI GPU drivers & devices
/opt/nemo-headunit/env/lib/dri/** r,
/opt/nemo-headunit/env/lib/** mr,
/dev/dri/** rw,
EOF
    if command -v apparmor_parser &>/dev/null; then
        apparmor_parser -r /etc/apparmor.d/usr.bin.surf >/dev/null 2>&1 || true
    fi
    echo -e "${GREEN}OK.${NC}"
fi

# 5. Configure Wayland labwc Direct Scanout & Compositor Optimizations
configure_labwc() {
    local rc_file="$1"
    local dir
    dir="$(dirname "$rc_file")"
    mkdir -p "$dir"
    if [ ! -f "$rc_file" ]; then
        cat <<'EOF' > "$rc_file"
<?xml version="1.0"?>
<labwc_config>
  <core>
    <allowDirectScanout>yes</allowDirectScanout>
  </core>
  <windowRules>
    <windowRule identifier="nemo-headunit" serverDecoration="no" />
  </windowRules>
</labwc_config>
EOF
    else
        if ! grep -qi "<allowDirectScanout>" "$rc_file"; then
            if grep -qi "<core>" "$rc_file"; then
                sed -i 's|<core>|<core>\n    <allowDirectScanout>yes</allowDirectScanout>|g' "$rc_file" 2>/dev/null || true
            else
                sed -i 's|<labwc_config>|<labwc_config>\n  <core>\n    <allowDirectScanout>yes</allowDirectScanout>\n  </core>|g' "$rc_file" 2>/dev/null || true
            fi
        fi
        if ! grep -qi "nemo-headunit" "$rc_file"; then
            if grep -qi "<windowRules>" "$rc_file"; then
                sed -i 's|<windowRules>|<windowRules>\n    <windowRule identifier="nemo-headunit" serverDecoration="no" />|g' "$rc_file" 2>/dev/null || true
            else
                sed -i 's|</labwc_config>|  <windowRules>\n    <windowRule identifier="nemo-headunit" serverDecoration="no" />\n  </windowRules>\n</labwc_config>|g' "$rc_file" 2>/dev/null || true
            fi
        fi
    fi
}

echo -n "  [gpu-detect] Configuring labwc Direct Scanout & zero-copy KMS scanout... "
configure_labwc "/etc/xdg/labwc/rc.xml"
for home_dir in /home/*; do
    if [ -d "$home_dir" ]; then
        configure_labwc "$home_dir/.config/labwc/rc.xml"
        chown -R "$(stat -c '%U:%G' "$home_dir")" "$home_dir/.config/labwc" 2>/dev/null || true
    fi
done
echo -e "${GREEN}OK.${NC}"

# 7. Configure system environment variables for VA-API and GStreamer
echo -n "  [gpu-detect] Configuring VA-API & GStreamer GPU acceleration environment... "
mkdir -p /etc/profile.d
cat <<'EOF' > /etc/profile.d/nemo-gpu.sh
# NemoHeadUnit GPU & VA-API Hardware Acceleration Environment
export QT_OPENGL=desktop
export QSG_RHI_BACKEND=opengl
export GST_VA_ALL_DRIVERS=1
export LIBVA_DRIVERS_PATH=/usr/lib/dri:/usr/lib/x86_64-linux-gnu/dri
EOF
chmod +x /etc/profile.d/nemo-gpu.sh
echo -e "${GREEN}OK.${NC}"

echo -e "${GREEN}[gpu-detect] GPU driver verification complete.${NC}"
