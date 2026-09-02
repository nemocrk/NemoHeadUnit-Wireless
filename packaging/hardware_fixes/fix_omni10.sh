#!/usr/bin/env bash
# fix_omni10.sh — HP Omni10 / Intel Bay Trail platform fixes & Boot Optimizations
#
# Fix 1: Audio loop (SOF debug + DSP driver override)
# Fix 2: GRUB (C-States cap & cmdline cleanup)
# Fix 3: Systemd Services (Masking wait-online/udev-settle, disabling bloat)
# Fix 4: SDDM early initialization safety net
# Fix 5: grubenv corruption fix
# Fix 6: Cloud-init purge
# Fix 7: Dracut (Early KMS & hostonly)
#
# Deve essere eseguito come root.
# Idempotente: controlla prima di modificare.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root: sudo bash $0" >&2
  exit 1
fi

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}  [hw-fix] HP Omni10 / Bay Trail fixes & boot optimizations${NC}"

AUDIO_CHANGED=0
GRUB_CHANGED=0
SERVICES_CHANGED=0
PKG_CHANGED=0
DRACUT_CHANGED=0

# ---------------------------------------------------------------------------
# Fix 1: Audio loop
# ---------------------------------------------------------------------------
AUDIO_CONF="/etc/modprobe.d/baytrail-audio-fix.conf"
echo -n "  [hw-fix] Audio loop fix... "

mkdir -p /etc/modprobe.d
if ! grep -q "options snd_sof sof_debug=1" "$AUDIO_CONF" 2>/dev/null; then
    echo "options snd_sof sof_debug=1" >> "$AUDIO_CONF"
    AUDIO_CHANGED=1
fi

if ! grep -q "options snd-intel-dspcfg dsp_driver=2" "$AUDIO_CONF" 2>/dev/null; then
    echo "options snd-intel-dspcfg dsp_driver=2" >> "$AUDIO_CONF"
    AUDIO_CHANGED=1
fi

if [ $AUDIO_CHANGED -eq 1 ]; then
    echo -e "${GREEN}applicato.${NC}"
else
    echo -e "${GREEN}già presente.${NC}"
fi

# ---------------------------------------------------------------------------
# Fix 2: System freeze — C-States cap via GRUB & Cmdline cleanup
# ---------------------------------------------------------------------------
GRUB_FILE="/etc/default/grub"
echo -n "  [hw-fix] GRUB configuration... "

if [ -f "$GRUB_FILE" ]; then
    # Add intel_idle.max_cstate=1, video=eDP-1:1920x1200@40, and mitigations=off (restores CPU speed on Atom Bay Trail)
    if ! grep -q "intel_idle.max_cstate=1" "$GRUB_FILE"; then
        sed -i 's/^\(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*\)"/\1 intel_idle.max_cstate=1"/' "$GRUB_FILE"
        sed -i "s/^\(GRUB_CMDLINE_LINUX_DEFAULT='[^']*\)'/\1 intel_idle.max_cstate=1'/" "$GRUB_FILE"
        GRUB_CHANGED=1
    fi

    # Disable CPU speculative execution mitigations (PTI/Spectre/Meltdown overhead on in-order Atom CPU)
    if ! grep -q "mitigations=off" "$GRUB_FILE"; then
        sed -i 's/^\(GRUB_CMDLINE_LINUX="[^"]*\)"/\1 mitigations=off"/' "$GRUB_FILE"
        sed -i "s/^\(GRUB_CMDLINE_LINUX='[^']*\)'/\1 mitigations=off'/" "$GRUB_FILE"
        GRUB_CHANGED=1
    fi

    # Ensure 40Hz panel refresh mode is configured (reduces memory scanout bandwidth 33%)
    if ! grep -q "video=eDP-1:1920x1200@40" "$GRUB_FILE"; then
        sed -i 's/video=eDP-1:[^ "]* \?//g' "$GRUB_FILE"
        sed -i 's/^\(GRUB_CMDLINE_LINUX="[^"]*\)"/\1 video=eDP-1:1920x1200@40"/' "$GRUB_FILE"
        sed -i "s/^\(GRUB_CMDLINE_LINUX='[^']*\)'/\1 video=eDP-1:1920x1200@40'/" "$GRUB_FILE"
        GRUB_CHANGED=1
    fi

    # Note: Modern Linux kernels (>=6.0) removed the obsolete module parameter 'i915.enable_rc6'.
    # Clean it up from GRUB cmdline to avoid kernel warning.
    if grep -q "i915\.enable_rc6" "$GRUB_FILE"; then
        sed -i 's/i915\.enable_rc6=[0-9]* \?//g' "$GRUB_FILE"
        GRUB_CHANGED=1
    fi

    # Cleanup redundant i915 and modules_load parameters from kernel cmdline
    if grep -qE "rd\.driver\.pre=i915|i915\.modeset=1|modules_load=" "$GRUB_FILE"; then
        sed -i 's/rd\.driver\.pre=i915 \?//g' "$GRUB_FILE"
        sed -i 's/i915\.modeset=1 \?//g' "$GRUB_FILE"
        sed -i 's/modules_load=[a-zA-Z0-9_,]* \?//g' "$GRUB_FILE"
        GRUB_CHANGED=1
    fi

    if [ $GRUB_CHANGED -eq 1 ]; then
        echo -e "${GREEN}applicato.${NC}"
        echo -n "  [hw-fix] Aggiornamento GRUB... "
        if command -v update-grub &>/dev/null; then
            update-grub >/dev/null 2>&1
            echo -e "${GREEN}OK (update-grub).${NC}"
        elif command -v grub-mkconfig &>/dev/null; then
            grub_cfg="/boot/grub/grub.cfg"
            if [ -f "/boot/efi/EFI/arch/grub.cfg" ]; then
                grub_cfg="/boot/efi/EFI/arch/grub.cfg"
            fi
            grub-mkconfig -o "$grub_cfg" >/dev/null 2>&1
            echo -e "${GREEN}OK (grub-mkconfig -> $grub_cfg).${NC}"
        elif command -v grub2-mkconfig &>/dev/null; then
            grub2-mkconfig -o /boot/grub2/grub.cfg >/dev/null 2>&1
            echo -e "${GREEN}OK (grub2-mkconfig).${NC}"
        else
            echo -e "${YELLOW}WARNING: update-grub o grub-mkconfig non trovato.${NC}"
        fi
    else
        echo -e "${GREEN}già configurato.${NC}"
    fi
else
    echo -e "${YELLOW}WARNING: $GRUB_FILE non trovato — fix saltato.${NC}"
fi

# ---------------------------------------------------------------------------
# Fix 3: Systemd Services Optimization
# ---------------------------------------------------------------------------
echo -n "  [hw-fix] Systemd services optimization... "

SERVICES_TO_MASK=(
    systemd-udev-settle.service
    NetworkManager-wait-online.service
)

SERVICES_TO_DISABLE=(
    networkd-dispatcher.service
    NetworkManager-dispatcher.service
    cups-browsed.service
    cups.service
    dnsmasq.service
    apport.service
    ModemManager.service
    sysstat.service
    ubuntu-advantage.service
    ua-reboot-cmds.service
)

for svc in "${SERVICES_TO_MASK[@]}"; do
    if [ "$(systemctl is-enabled "$svc" 2>/dev/null)" != "masked" ]; then
        systemctl mask "$svc" >/dev/null 2>&1 || true
        SERVICES_CHANGED=1
    fi
done

for svc in "${SERVICES_TO_DISABLE[@]}"; do
    if systemctl is-enabled "$svc" &>/dev/null; then
        systemctl disable --now "$svc" >/dev/null 2>&1 || true
        SERVICES_CHANGED=1
    fi
done

if [ $SERVICES_CHANGED -eq 1 ]; then
    echo -e "${GREEN}applicato.${NC}"
else
    echo -e "${GREEN}già ottimizzati.${NC}"
fi

# ---------------------------------------------------------------------------
# Fix 4: SDDM ordering safety net
# ---------------------------------------------------------------------------
echo -n "  [hw-fix] SDDM ordering safety net... "
SDDM_DIR="/etc/systemd/system/sddm.service.d"
SDDM_CONF="$SDDM_DIR/override.conf"

mkdir -p "$SDDM_DIR"
TEMP_SDDM=$(mktemp)
cat > "$TEMP_SDDM" <<'EOF'
[Unit]
After=dev-dri-card1.device
Wants=dev-dri-card1.device
EOF

if [ ! -f "$SDDM_CONF" ] || ! cmp -s "$TEMP_SDDM" "$SDDM_CONF"; then
    mv "$TEMP_SDDM" "$SDDM_CONF"
    chmod 644 "$SDDM_CONF"
    systemctl daemon-reload >/dev/null 2>&1 || true
    echo -e "${GREEN}configurato.${NC}"
else
    rm "$TEMP_SDDM"
    echo -e "${GREEN}già presente.${NC}"
fi

# ---------------------------------------------------------------------------
# Fix 5: Failed units (grubenv)
# ---------------------------------------------------------------------------
echo -n "  [hw-fix] Verifica grubenv... "
if ! grub-editenv list >/dev/null 2>&1; then
    grub-editenv /boot/grub/grubenv create
    systemctl reset-failed grub2-common.service >/dev/null 2>&1 || true
    echo -e "${GREEN}ricreato.${NC}"
else
    echo -e "${GREEN}integro.${NC}"
fi

# ---------------------------------------------------------------------------
# Fix 6: Disabilitare cloud-init (APT-lock safe)
# ---------------------------------------------------------------------------
echo -n "  [hw-fix] Disattivazione cloud-init... "

# Poiché lo script potrebbe girare durante un setup APT (es. chroot/post-install),
# non usiamo apt-get purge per evitare l'errore "dpkg lock".
# Creare il file .disabled è il metodo ufficiale per spegnere cloud-init in modo sicuro.
mkdir -p /etc/cloud
if [ ! -f /etc/cloud/cloud-init.disabled ]; then
    touch /etc/cloud/cloud-init.disabled
    PKG_CHANGED=1
    echo -e "${GREEN}disabilitato tramite flag.${NC}"
else
    echo -e "${GREEN}già disabilitato.${NC}"
fi

# ---------------------------------------------------------------------------
# Fix 7: Dracut (Early KMS e hostonly)
# ---------------------------------------------------------------------------
DRACUT_CONF="/etc/dracut.conf.d/10-early-kms.conf"
echo -n "  [hw-fix] Dracut Early KMS & hostonly... "

mkdir -p /etc/dracut.conf.d
TEMP_DRACUT=$(mktemp)
echo 'force_drivers+=" i915 mmc_block sdhci sdhci-acpi "' > "$TEMP_DRACUT"
echo 'hostonly="yes"' >> "$TEMP_DRACUT"

if [ ! -f "$DRACUT_CONF" ] || ! cmp -s "$TEMP_DRACUT" "$DRACUT_CONF"; then
    mv "$TEMP_DRACUT" "$DRACUT_CONF"
    chmod 644 "$DRACUT_CONF"
    DRACUT_CHANGED=1
    echo -e "${GREEN}configurato.${NC}"
else
    rm "$TEMP_DRACUT"
    echo -e "${GREEN}già presente.${NC}"
fi

if [ $DRACUT_CHANGED -eq 1 ]; then
    echo -n "  [hw-fix] Rigenerazione initramfs (dracut)... "
    if command -v dracut &>/dev/null; then
        dracut --force >/dev/null 2>&1
        echo -e "${GREEN}OK.${NC}"
    else
        echo -e "${YELLOW}WARNING: dracut non trovato.${NC}"
    fi
fi

# ---------------------------------------------------------------------------
# Fix 8: GPU Performance & Power Stability (RC6 / Runtime PM / Frequency floor)
# ---------------------------------------------------------------------------
echo -n "  [hw-fix] GPU unthrottle & RC6 / power stability... "
GPU_CHANGED=0

# Disable GPU runtime PM (disables RC6 power-collapse sleep on Bay Trail)
# and raise minimum GPU clock floor to 400 MHz (burst to 667 MHz)
UDEV_GPU_RULES="/etc/udev/rules.d/99-gpu-performance.rules"
mkdir -p /etc/udev/rules.d
TEMP_UDEV=$(mktemp)
cat > "$TEMP_UDEV" <<'EOF'
# Disable GPU runtime power collapse (RC6) and lock frequency floor to 400 MHz
ACTION=="add", SUBSYSTEM=="drm", KERNEL=="card1", DRIVERS=="i915", ATTR{power/control}="on", ATTR{gt_min_freq_mhz}="400", ATTR{gt_boost_freq_mhz}="667"
EOF

if [ ! -f "$UDEV_GPU_RULES" ] || ! cmp -s "$TEMP_UDEV" "$UDEV_GPU_RULES"; then
    mv "$TEMP_UDEV" "$UDEV_GPU_RULES"
    chmod 644 "$UDEV_GPU_RULES"
    udevadm trigger -s drm >/dev/null 2>&1 || true
    GPU_CHANGED=1
else
    rm -f "$TEMP_UDEV"
fi

# Apply immediately if card1 exists
if [ -d "/sys/class/drm/card1" ]; then
    echo "on" > /sys/class/drm/card1/power/control 2>/dev/null || true
    echo "400" > /sys/class/drm/card1/gt_min_freq_mhz 2>/dev/null || true
fi

# Set QT_SCALE_FACTOR=1.5 for crisp 1280x800 logical viewport
if ! grep -q "QT_SCALE_FACTOR=1.5" /etc/environment 2>/dev/null; then
    echo "QT_SCALE_FACTOR=1.5" >> /etc/environment
    echo "QT_ENABLE_HIGHDPI_SCALING=0" >> /etc/environment
    GPU_CHANGED=1
fi

if [ -d "/home/nemo" ]; then
    mkdir -p /home/nemo/.config/labwc
    if ! grep -q "QT_SCALE_FACTOR=1.5" /home/nemo/.config/labwc/environment 2>/dev/null; then
        echo "QT_SCALE_FACTOR=1.5" >> /home/nemo/.config/labwc/environment
        chown -R nemo:nemo /home/nemo/.config 2>/dev/null || true
        GPU_CHANGED=1
    fi
    # Disable Xwayland in labwc to avoid missing binary warning and save RAM
    LABWC_RC="/home/nemo/.config/labwc/rc.xml"
    if [ ! -f "$LABWC_RC" ] || ! grep -q "<xwayland>" "$LABWC_RC"; then
        cat <<'EOF' > "$LABWC_RC"
<?xml version="1.0"?>
<labwc_config>
  <core>
    <xwayland>no</xwayland>
    <allowDirectScanout>yes</allowDirectScanout>
  </core>
  <windowRules>
    <windowRule identifier="nemo-headunit" serverDecoration="no" />
  </windowRules>
</labwc_config>
EOF
        chown -R nemo:nemo /home/nemo/.config 2>/dev/null || true
        GPU_CHANGED=1
    fi
fi

if [ $GPU_CHANGED -eq 1 ]; then
    echo -e "${GREEN}applicato.${NC}"
else
    echo -e "${GREEN}già configurato.${NC}"
fi

# ---------------------------------------------------------------------------
# Fix 9: Bluetooth persistent MAC address across reboots
# ---------------------------------------------------------------------------
echo -n "  [hw-fix] Bluetooth persistent MAC address service... "
BT_MAC_CHANGED=0
BT_ADDR_FILE="/etc/bluetooth/bdaddr"
BT_SERVICE_FILE="/etc/systemd/system/bluetooth-persistent-mac.service"

mkdir -p /etc/bluetooth

# If no persistent MAC stored yet, derive one deterministically from wlan0 MAC
# (e.g. WiFi 78:61:7c:93:13:74 -> BT 78:61:7c:93:13:75) or generate one
if [ ! -s "${BT_ADDR_FILE}" ]; then
    NEW_MAC=""
    if [ -f "/sys/class/net/wlan0/address" ]; then
        WIFI_MAC="$(cat /sys/class/net/wlan0/address 2>/dev/null | tr 'a-z' 'A-Z')"
        # Invert lowest bit of last octet to stay in same registered OUI
        PREFIX="${WIFI_MAC%:*}"
        LAST_HEX="${WIFI_MAC##*:}"
        LAST_INT=$(( 16#$LAST_HEX ^ 1 ))
        NEW_MAC=$(printf "%s:%02X" "${PREFIX}" "${LAST_INT}")
    fi
    if [ -z "${NEW_MAC}" ]; then
        # Fallback random locally-administered unicast MAC
        NEW_MAC=$(hexdump -n3 -e'/3 "02:00:00"' -e'/3 ":%02X:%02X:%02X"' /dev/urandom 2>/dev/null || echo "78:61:7C:93:13:75")
    fi
    echo "${NEW_MAC}" > "${BT_ADDR_FILE}"
    chmod 644 "${BT_ADDR_FILE}"
    BT_MAC_CHANGED=1
fi

TEMP_BT_SVC=$(mktemp)
cat > "$TEMP_BT_SVC" <<'EOF'
[Unit]
Description=Set Persistent Bluetooth MAC Address (Broadcom/BCM)
Before=bluetooth.service

[Service]
Type=oneshot
RemainAfterExit=yes
TimeoutStartSec=10
ExecStart=/bin/bash -c ' \
    ADDR="$(cat /etc/bluetooth/bdaddr 2>/dev/null | tr -d " \n\r")"; \
    [ -n "$ADDR" ] || exit 0; \
    command -v btmgmt >/dev/null 2>&1 || exit 0; \
    for i in {1..30}; do \
        if btmgmt --index 0 info >/dev/null 2>&1; then \
            btmgmt --index 0 power off >/dev/null 2>&1 || true; \
            btmgmt --index 0 public-addr "$ADDR" >/dev/null 2>&1 || true; \
            btmgmt --index 0 power on >/dev/null 2>&1 || true; \
            exit 0; \
        fi; \
        sleep 0.2; \
    done'

[Install]
WantedBy=bluetooth.service
EOF

if [ ! -f "$BT_SERVICE_FILE" ] || ! cmp -s "$TEMP_BT_SVC" "$BT_SERVICE_FILE"; then
    mv "$TEMP_BT_SVC" "$BT_SERVICE_FILE"
    chmod 644 "$BT_SERVICE_FILE"
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl enable bluetooth-persistent-mac.service >/dev/null 2>&1 || true
    BT_MAC_CHANGED=1
else
    rm -f "$TEMP_BT_SVC"
fi

if [ $BT_MAC_CHANGED -eq 1 ]; then
    echo -e "${GREEN}applicato ($(cat "${BT_ADDR_FILE}")).${NC}"
else
    echo -e "${GREEN}già presente ($(cat "${BT_ADDR_FILE}")).${NC}"
fi

# ---------------------------------------------------------------------------
# Riepilogo
# ---------------------------------------------------------------------------
echo ""
echo -e "  [hw-fix] Info:"
echo "    - bluetooth / wpa_supplicant / org.nemo.APManager : Mantenuti attivi."
echo "    - cups.service è stato disabilitato per performance."
echo "    - Display impostato a 1920x1200@40Hz (-33% scanout bandwidth)."
echo "    - GPU RC6 / Runtime PM disabilitato; clock minimo fissato a 400MHz."
echo "    - Bluetooth MAC persistente in ${BT_ADDR_FILE} tramite bluetooth-persistent-mac.service."

if [ $AUDIO_CHANGED -eq 1 ] || [ $GRUB_CHANGED -eq 1 ] || [ $SERVICES_CHANGED -eq 1 ] || [ $PKG_CHANGED -eq 1 ] || [ $DRACUT_CHANGED -eq 1 ] || [ $GPU_CHANGED -eq 1 ] || [ $BT_MAC_CHANGED -eq 1 ]; then
    echo -e "  ${GREEN}[hw-fix] HP Omni10: fix applicati. Riavvio necessario.${NC}"
else
    echo -e "  ${GREEN}[hw-fix] HP Omni10: nessuna modifica necessaria.${NC}"
fi