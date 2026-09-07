#!/usr/bin/env bash
# fix_omni10.sh — HP Omni10 / Intel Bay Trail platform fixes & Boot Optimizations
#
# Fix 1: Audio loop (SOF debug + DSP driver override)
# Fix 2: GRUB (C-States cap & cmdline cleanup)
# Fix 3: Systemd Services (Masking wait-online/udev-settle, disabling bloat)
# Fix 4: SDDM early initialization safety net
# Fix 5: grubenv corruption fix
# Fix 6: Cloud-init purge
# Fix 7: Dracut / mkinitcpio (Early KMS & hostonly)
# Fix 8: GPU Performance & Power Stability (RC6 / Runtime PM / Frequency floor)
# Fix 9: Bluetooth persistent MAC address across reboots
# Fix 10: Vendor firmware (Broadcom BT/WiFi, Intel SST DSP)
# Fix 11: Hardware quirks environment
# Fix 12: PipeWire & WirePlumber autostart + unmuting speakers/headphones
# Fix 13: Physical volume buttons (Crystal Cove PMIC unmask)
# Fix 14: Broadcom Bluetooth SCO Audio Routing to HCI UART (HFP voice call fix)
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
BUTTONS_CHANGED=0
BCM_SCO_CHANGED=0

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

    # Set Windows 2013 (Windows 8.1) ACPI OSI profile for factory HP Omni 10 DSDT device routing
    if ! grep -q 'acpi_osi="!Windows 2015"' "$GRUB_FILE" || ! grep -q 'acpi_osi="Windows 2013"' "$GRUB_FILE"; then
        # Convert double-quoted to single-quoted if needed to allow internal quotes safely
        sed -i "s/^GRUB_CMDLINE_LINUX=\"\(.*\)\"$/GRUB_CMDLINE_LINUX='\1'/" "$GRUB_FILE"
        sed -i "s/^\(GRUB_CMDLINE_LINUX='[^']*\)'/\1 acpi_osi=\"!Windows 2015\" acpi_osi=\"Windows 2013\"'/" "$GRUB_FILE"
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

    # Set GRUB_TIMEOUT=0 and GRUB_TIMEOUT_STYLE=hidden to boot instantly without menu
    if ! grep -q "^GRUB_TIMEOUT=0" "$GRUB_FILE"; then
        sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=0/' "$GRUB_FILE"
        GRUB_CHANGED=1
    fi
    if ! grep -q "^GRUB_TIMEOUT_STYLE=hidden" "$GRUB_FILE"; then
        if grep -q "^GRUB_TIMEOUT_STYLE=" "$GRUB_FILE"; then
            sed -i 's/^GRUB_TIMEOUT_STYLE=.*/GRUB_TIMEOUT_STYLE=hidden/' "$GRUB_FILE"
        else
            echo "GRUB_TIMEOUT_STYLE=hidden" >> "$GRUB_FILE"
        fi
        GRUB_CHANGED=1
    fi

    # Set GRUB_RECORDFAIL_TIMEOUT=0 (prevent 30s delay on failed boots / power loss)
    if ! grep -q "^GRUB_RECORDFAIL_TIMEOUT=0" "$GRUB_FILE"; then
        if grep -q "^GRUB_RECORDFAIL_TIMEOUT=" "$GRUB_FILE"; then
            sed -i 's/^GRUB_RECORDFAIL_TIMEOUT=.*/GRUB_RECORDFAIL_TIMEOUT=0/' "$GRUB_FILE"
        else
            echo "GRUB_RECORDFAIL_TIMEOUT=0" >> "$GRUB_FILE"
        fi
        GRUB_CHANGED=1
    fi

    # Ensure quiet splash and suppress VT blinking cursor / console messages
    for param in "quiet" "splash" "vt.global_cursor_default=0" "rd.systemd.show_status=false" "systemd.show_status=false"; do
        if ! grep -q "$param" "$GRUB_FILE"; then
            sed -i "s/^\(GRUB_CMDLINE_LINUX_DEFAULT=\"[^\"]*\)\"/\1 $param\"/" "$GRUB_FILE"
            sed -i "s/^\(GRUB_CMDLINE_LINUX_DEFAULT='[^']*\)'/\1 $param'/" "$GRUB_FILE"
            GRUB_CHANGED=1
        fi
    done

    # Silence "Loading Linux ..." and "Loading initial ramdisk ..." text output from GRUB 10_linux template
    if [ -f "/etc/grub.d/10_linux" ]; then
        if grep -qE "echo.*echo \"\\\$message\"" /etc/grub.d/10_linux; then
            sed -i 's/^[ \t]*echo[ \t]*\x27\$(echo "\$message"/        # echo \x27\$(echo "\$message"/g' /etc/grub.d/10_linux
            GRUB_CHANGED=1
        fi
    fi

    if [ -f "/boot/grub/grubenv" ] && command -v grub-editenv &>/dev/null; then
        grub-editenv /boot/grub/grubenv unset recordfail >/dev/null 2>&1 || true
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
# Fix 2b: Plymouth Boot Splash (ACPI BGRT Theme & Mask Early Termination)
# ---------------------------------------------------------------------------
echo -n "  [hw-fix] Plymouth BGRT splash & handoff... "
PLYMOUTH_CHANGED=0
if command -v plymouth-set-default-theme &>/dev/null; then
    current_theme="$(plymouth-set-default-theme 2>/dev/null || echo "")"
    if [ "$current_theme" != "bgrt" ] && [ -d "/usr/share/plymouth/themes/bgrt" ]; then
        plymouth-set-default-theme -R bgrt >/dev/null 2>&1 || true
        PLYMOUTH_CHANGED=1
    fi
fi
if systemctl is-enabled plymouth-quit.service &>/dev/null || [ -f "/usr/lib/systemd/system/plymouth-quit.service" ]; then
    if ! systemctl is-enabled plymouth-quit.service 2>/dev/null | grep -q "masked"; then
        systemctl mask plymouth-quit.service plymouth-quit-wait.service >/dev/null 2>&1 || true
        PLYMOUTH_CHANGED=1
    fi
fi

# Allow nemo user to dismiss plymouth cleanly without password
if [ -d "/etc/sudoers.d" ]; then
    SUDOERS_FILE="/etc/sudoers.d/nemo-plymouth"
    if [ ! -f "$SUDOERS_FILE" ] || ! grep -q "/usr/bin/plymouth" "$SUDOERS_FILE"; then
        echo 'nemo ALL=(ALL) NOPASSWD: /usr/bin/plymouth' > "$SUDOERS_FILE"
        chmod 0440 "$SUDOERS_FILE"
        PLYMOUTH_CHANGED=1
    fi
fi

if [ $PLYMOUTH_CHANGED -eq 1 ]; then
    echo -e "${GREEN}configurato (bgrt + mask plymouth-quit + sudoers).${NC}"
else
    echo -e "${GREEN}già configurato.${NC}"
fi

# ---------------------------------------------------------------------------
# Fix 3: Systemd Services Optimization
# ---------------------------------------------------------------------------
echo -n "  [hw-fix] Systemd services optimization... "

SERVICES_TO_MASK=(
    systemd-udev-settle.service
    NetworkManager-wait-online.service
    dev-tpm0.device
    dev-tpmrm0.device
    tpm2.target
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
# Fix 7B: Mkinitcpio (Early KMS, eMMC Storage & I2C Input Fast-boot)
# ---------------------------------------------------------------------------
MKINIT_CONF="/etc/mkinitcpio.conf"
echo -n "  [hw-fix] Mkinitcpio Early KMS & Input modules... "

MKINIT_CHANGED=0
if [ -f "$MKINIT_CONF" ]; then
    # Moduli ideali per la piattaforma Bay Trail (Omni10) per azzerare i colli di bottiglia
    TARGET_MODULES="i915 sdhci_acpi mmc_block"
    
    # Estrae l'attuale riga MODULES=(...)
    CURRENT_MODULES_LINE=$(grep -E "^MODULES=\(" "$MKINIT_CONF" || echo "")
    
    # Verifica se tutti i moduli target sono già presenti nella stringa
    NEED_UPDATE=0
    for mod in $TARGET_MODULES; do
        if [[ ! "$CURRENT_MODULES_LINE" =~ $mod ]]; then
            NEED_UPDATE=1
            break
        fi
    done

    if [ $NEED_UPDATE -eq 1 ]; then
        # Sostituisce l'intera riga MODULES=(...) iniettando la combinazione ottimale
        sed -i "s/^MODULES=(.*/MODULES=($TARGET_MODULES)/" "$MKINIT_CONF"
        MKINIT_CHANGED=1
        echo -e "${GREEN}configurato in mkinitcpio.conf.${NC}"
    else
        echo -e "${GREEN}già ottimizzato.${NC}"
    fi
else
    echo -e "${YELLOW}WARNING: $MKINIT_CONF non trovato — fix saltato.${NC}"
fi

# Se il file di configurazione è stato modificato, rigenera i ramdisk di Arch
if [ $MKINIT_CHANGED -eq 1 ]; then
    echo -n "  [hw-fix] Rigenerazione initramfs (mkinitcpio)... "
    if command -v mkinitcpio &>/dev/null; then
        mkinitcpio -P >/dev/null 2>&1
        echo -e "${GREEN}OK.${NC}"
    else
        echo -e "${YELLOW}WARNING: mkinitcpio non trovato.${NC}"
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

# Lock CPU governor to 'performance' so Atom Bay Trail cores do not throttle down to 533 MHz
UDEV_CPU_RULES="/etc/udev/rules.d/98-cpu-governor.rules"
TEMP_CPU_UDEV=$(mktemp)
cat > "$TEMP_CPU_UDEV" <<'EOF'
ACTION=="add|change", SUBSYSTEM=="cpu", KERNEL=="cpu[0-9]*", ATTR{cpufreq/scaling_governor}="performance"
EOF

if [ ! -f "$UDEV_CPU_RULES" ] || ! cmp -s "$TEMP_CPU_UDEV" "$UDEV_CPU_RULES"; then
    mv "$TEMP_CPU_UDEV" "$UDEV_CPU_RULES"
    chmod 644 "$UDEV_CPU_RULES"
    udevadm trigger --subsystem-match=cpu >/dev/null 2>&1 || true
    for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        echo "performance" > "$g" 2>/dev/null || true
    done
else
    rm -f "$TEMP_CPU_UDEV"
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
After=bluetooth.service
Requires=bluetooth.service

[Unit]
Description=Set Persistent Bluetooth MAC Address (Broadcom/BCM con Log Verbose)
After=bluetooth.service
Requires=bluetooth.service
Before=nemo-kiosk.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c '\
  ADDR="$(tr -d "[:space:]" < /etc/bluetooth/bdaddr 2>/dev/null)"; \
  if [ -z "$ADDR" ]; then echo "BDADDR non trovato" >&2; exit 1; fi; \
  echo ">>> MAC configurato da file: $ADDR"; \
  \
  echo "--- FASE 1: Ricerca controller e attesa indice valido ---"; \
  FOUND=0; \
  for i in {1..30}; do \
    OUT_INFO="$(btmgmt --index 0 info 2>&1)"; \
    if echo "$OUT_INFO" | grep -qi "Supported options"; then \
      echo "Tentativo $i: Controller pronto."; \
      FOUND=1; \
      break; \
    fi; \
    sleep 0.5; \
  done; \
  if [ $FOUND -eq 0 ]; then \
    echo "ERRORE FASE 1: Controller non pronto. Ultimo output:" ; \
    echo "$OUT_INFO"; \
    exit 1; \
  fi; \
  \
  echo "--- FASE 2: Spegnimento e Iniezione MAC ($ADDR) ---"; \
  btmgmt --index 0 power off >/dev/null 2>&1 || true; \
  \
  OUT_MAC="$(btmgmt --index 0 public-addr "$ADDR" 2>&1)"; \
  echo "public-addr -> $OUT_MAC"; \
  if echo "$OUT_MAC" | grep -qi "failed"; then \
    echo "ERRORE FASE 2: Rifiuto del MAC da parte del controller." >&2; \
    exit 1; \
  fi; \
  \
  sleep 1; \
  echo "--- FASE 2.5: Verifica immediata pre-reset ---"; \
  OUT_PRE="$(btmgmt --index 0 info 2>&1)"; \
  echo "Stato pre-reset ->" "$OUT_PRE"; \
  \
  echo "--- FASE 3: Forzatura reset hardware UART (unbind/bind) ---"; \
  echo "serial0-0" > /sys/bus/serial/drivers/hci_uart_bcm/unbind 2>/dev/null || true; \
  sleep 1; \
  echo "serial0-0" > /sys/bus/serial/drivers/hci_uart_bcm/bind 2>/dev/null || true; \
  \
  echo "--- FASE 4: Test di riscontro post-rebind ---"; \
  sleep 2; \
  FOUND_FINAL=0; \
  for i in {1..20}; do \
    OUT_FINAL="$(btmgmt --index 0 info 2>&1)"; \
    if echo "$OUT_FINAL" | grep -qi "Supported options"; then \
      btmgmt --index 0 power on >/dev/null 2>&1 || true; \
      echo "SUCCESSO: Controller resuscitato e operativo con MAC $ADDR."; \
      python3 /usr/local/bin/bcm-sco-routing.py >/dev/null 2>&1 || true; \
      FOUND_FINAL=1; \
      break; \
    fi; \
    sleep 0.5; \
  done; \
  if [ $FOUND_FINAL -eq 0 ]; then \
    echo "ERRORE CRITICO: Il controller non si è ripreso dal rebind."; \
    echo "$OUT_FINAL"; \
    exit 1; \
  fi'

[Install]
WantedBy=multi-user.target
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
# Fix 10: Vendor Firmware (Broadcom BT/WiFi NVRAM & Intel SST Audio DSP)
# ---------------------------------------------------------------------------
echo -n "  [hw-fix] Proprietary / vendor firmware (Broadcom & Intel SST DSP)... "
FW_CHANGED=0

mkdir -p /lib/firmware/brcm/
mkdir -p /lib/firmware/intel/

FW_TARGETS=(
    "/lib/firmware/brcm/BCM4324B3.hcd|https://raw.githubusercontent.com/Asus-T100/firmware/master/brcm/BCM4324B3.hcd"
    "/lib/firmware/brcm/brcmfmac43241b4-sdio.txt|https://raw.githubusercontent.com/Asus-T100/firmware/master/brcm/brcmfmac43241b4-sdio.txt"
    "/lib/firmware/intel/fw_sst_0f28.bin|https://raw.githubusercontent.com/Asus-T100/firmware/master/intel/fw_sst_0f28.bin"
    "/lib/firmware/intel/fw_sst_0f28_ssp0.bin|https://raw.githubusercontent.com/Asus-T100/firmware/master/intel/fw_sst_0f28_ssp0.bin"
)

FW_DOWNLOADED=0
for entry in "${FW_TARGETS[@]}"; do
    IFS="|" read -r dest_file url <<< "$entry"
    if [ ! -s "$dest_file" ]; then
        TEMP_DL=$(mktemp)
        if curl -fsSL -o "$TEMP_DL" "$url"; then
            if [ -s "$TEMP_DL" ]; then
                mv "$TEMP_DL" "$dest_file"
                chmod 644 "$dest_file"
                FW_CHANGED=1
                FW_DOWNLOADED=$((FW_DOWNLOADED + 1))
            else
                rm -f "$TEMP_DL"
            fi
        else
            rm -f "$TEMP_DL"
            echo -e "\n    ${YELLOW}WARNING: Impossibile scaricare $(basename "$dest_file") da $url${NC}" >&2
        fi
    fi
done

if [ $FW_CHANGED -eq 1 ]; then
    echo -e "${GREEN}scaricati e installati ($FW_DOWNLOADED file).${NC}"
else
    echo -e "${GREEN}già presenti.${NC}"
fi

# ---------------------------------------------------------------------------
# Fix 11: NemoHeadUnit Hardware Quirks Environment
# ---------------------------------------------------------------------------
echo -n "  [hw-fix] NemoHeadUnit hardware quirks environment... "
QUIRKS_DIR="/etc/nemo-headunit"
QUIRKS_FILE="$QUIRKS_DIR/hardware_quirks.env"
QUIRKS_CHANGED=0

mkdir -p "$QUIRKS_DIR"
cat <<'EOF' > "$QUIRKS_DIR/hardware_quirks.env.tmp"
# HP Omni 10 Hardware-specific Quirks & Tunings for NemoHeadUnit
LIBVA_DRIVER_NAME="i965"
QT_SCALE_FACTOR="1.5"
NEMO_GST_ZERO_COPY_PIPELINE="appsrc name=src is-live=true format=bytes ! h264parse config-interval=-1 ! vah264dec ! vapostproc add-borders=true ! video/x-raw(memory:DMABuf),format=DMA_DRM,drm-format=YV12,width=1280 ! glupload ! qml6glsink name=qml_sink sync=false"
EOF

if [ ! -f "$QUIRKS_FILE" ] || ! cmp -s "$QUIRKS_DIR/hardware_quirks.env.tmp" "$QUIRKS_FILE"; then
    mv "$QUIRKS_DIR/hardware_quirks.env.tmp" "$QUIRKS_FILE"
    chmod 644 "$QUIRKS_FILE"
    QUIRKS_CHANGED=1
    echo -e "${GREEN}generato in $QUIRKS_FILE.${NC}"
else
    rm -f "$QUIRKS_DIR/hardware_quirks.env.tmp"
    echo -e "${GREEN}già presente.${NC}"
fi

# ---------------------------------------------------------------------------
# Fix 12: PipeWire & WirePlumber Audio Autostart (User lingering & systemd user services)
# ---------------------------------------------------------------------------
echo -n "  [hw-fix] PipeWire & WirePlumber audio autostart... "
AUDIO_AUTOSTART_CHANGED=0

# 1. Enable PipeWire and WirePlumber globally for all user sessions
if command -v systemctl &>/dev/null; then
    for unit in pipewire.socket pipewire-pulse.socket pipewire.service pipewire-pulse.service wireplumber.service; do
        if systemctl --global is-enabled "$unit" 2>/dev/null | grep -qv "enabled"; then
            systemctl --global enable "$unit" >/dev/null 2>&1 || true
            AUDIO_AUTOSTART_CHANGED=1
        fi
    done
fi

# 2. Ensure /etc/systemd/user target symlinks are present
mkdir -p /etc/systemd/user/default.target.wants /etc/systemd/user/sockets.target.wants
for svc in pipewire.service pipewire-pulse.service wireplumber.service; do
    if [ -f "/usr/lib/systemd/user/${svc}" ] && [ ! -L "/etc/systemd/user/default.target.wants/${svc}" ]; then
        ln -sf "/usr/lib/systemd/user/${svc}" "/etc/systemd/user/default.target.wants/${svc}"
        AUDIO_AUTOSTART_CHANGED=1
    fi
done
for sct in pipewire.socket pipewire-pulse.socket; do
    if [ -f "/usr/lib/systemd/user/${sct}" ] && [ ! -L "/etc/systemd/user/sockets.target.wants/${sct}" ]; then
        ln -sf "/usr/lib/systemd/user/${sct}" "/etc/systemd/user/sockets.target.wants/${sct}"
        AUDIO_AUTOSTART_CHANGED=1
    fi
done

# 3. Enable loginctl user lingering for standard users so audio services start at boot
while IFS=: read -r username _ uid _ _ homedir _; do
    if [ "${uid}" -ge 1000 ] && [ "${uid}" -lt 60000 ]; then
        if command -v loginctl &>/dev/null; then
            if ! loginctl show-user "$username" 2>/dev/null | grep -q "Linger=yes"; then
                loginctl enable-linger "$username" >/dev/null 2>&1 || true
                AUDIO_AUTOSTART_CHANGED=1
            fi
        fi
        # Start immediately if user session manager is running
        if command -v systemctl &>/dev/null; then
            systemctl --user -M "${username}@" start pipewire.socket pipewire-pulse.socket pipewire.service wireplumber.service >/dev/null 2>&1 || true
        fi
    fi
done < <(getent passwd)

# 4. Ensure ALSA hardware mixer routes (Speakers and Headphone/AUX jack) are unmuted
if command -v amixer &>/dev/null; then
    amixer -c bytcrrt5640 sset 'Speaker' unmute 100% >/dev/null 2>&1 || true
    amixer -c bytcrrt5640 sset 'Headphone' unmute 100% >/dev/null 2>&1 || true
    if command -v alsactl &>/dev/null; then
        alsactl store >/dev/null 2>&1 || true
    fi
fi

if [ $AUDIO_AUTOSTART_CHANGED -eq 1 ]; then
    echo -e "${GREEN}applicato (lingering + pipewire/wireplumber abilitati).${NC}"
else
    echo -e "${GREEN}già configurato.${NC}"
fi

# ---------------------------------------------------------------------------
# Fix 13: Physical Volume Buttons & Crystal Cove PMIC Initialization
# ---------------------------------------------------------------------------
echo -n "  [hw-fix] Physical volume buttons (PMIC unmask & boot service)... "

# 1. Ensure i2c-dev and button array modules load at boot
mkdir -p /etc/modules-load.d
BUTTON_MODULES_CONF="/etc/modules-load.d/omni10-buttons.conf"
BUTTON_MODULES_CONTENT="i2c-dev
soc_button_array"

if [ ! -f "$BUTTON_MODULES_CONF" ] || [ "$(cat "$BUTTON_MODULES_CONF")" != "$BUTTON_MODULES_CONTENT" ]; then
    echo "$BUTTON_MODULES_CONTENT" > "$BUTTON_MODULES_CONF"
    BUTTONS_CHANGED=1
fi
modprobe i2c-dev >/dev/null 2>&1 || true
modprobe soc_button_array >/dev/null 2>&1 || true

# 2. Ensure i2c-tools package is installed
if ! command -v i2cset &>/dev/null; then
    if command -v pacman &>/dev/null; then
        pacman -S --needed --noconfirm i2c-tools >/dev/null 2>&1 || true
        BUTTONS_CHANGED=1
    elif command -v apt-get &>/dev/null; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq i2c-tools >/dev/null 2>&1 || true
        BUTTONS_CHANGED=1
    fi
fi

# 3. Enable 5-button array module option if intel_hid is used
mkdir -p /etc/modprobe.d
if ! grep -q "options intel_hid enable_5_button_array=1" /etc/modprobe.d/omni10-buttons.conf 2>/dev/null; then
    echo "options intel_hid enable_5_button_array=1" > /etc/modprobe.d/omni10-buttons.conf
    BUTTONS_CHANGED=1
fi

# 4. Generate PMIC initialization script to unmask Crystal Cove side buttons and bind driver
INIT_SCRIPT="/usr/local/bin/hp-omni10-buttons-init.sh"
cat <<'EOF' > /tmp/hp-omni10-buttons-init.sh.tmp
#!/usr/bin/env bash
# Unmasks Crystal Cove PMIC panel control (reg 0x52 / FCOT) and GPIO 0/1 interrupt lines
modprobe i2c-dev >/dev/null 2>&1 || true

# Find I2C adapter bus number for INT33FD (Crystal Cove PMIC)
PMIC_PATH=$(find /sys/bus/i2c/devices/ -name '*INT33FD*' 2>/dev/null | head -n 1)
BUS=""
if [ -n "$PMIC_PATH" ]; then
    PARENT_DEV=$(readlink -f "$PMIC_PATH" 2>/dev/null || true)
    if [ -n "$PARENT_DEV" ]; then
        BUS_NAME=$(basename "$(dirname "$PARENT_DEV")")
        BUS="${BUS_NAME#i2c-}"
    fi
fi

if [ -z "$BUS" ] || ! [[ "$BUS" =~ ^[0-9]+$ ]]; then
    BUS=6
fi

if command -v i2cset &>/dev/null; then
    # Register 0x52 (GPIOPANELCTL / FCOT): Set bit 0 to 1 (enables side buttons power/logic)
    i2cset -y -f "$BUS" 0x6e 0x52 0x01 >/dev/null 2>&1 || true
    # Register 0x0E (MIRQLVL1): Unmask GPIO level 1 interrupt (bit 5 = 0)
    i2cset -y -f "$BUS" 0x6e 0x0e 0x5f >/dev/null 2>&1 || true
    # Register 0x19 (MGPIO0IRQS0): Unmask GPIO 0 & 1 (bits 0,1 = 0)
    i2cset -y -f "$BUS" 0x6e 0x19 0xfc >/dev/null 2>&1 || true
fi

exit 0
EOF

if [ ! -f "$INIT_SCRIPT" ] || ! cmp -s /tmp/hp-omni10-buttons-init.sh.tmp "$INIT_SCRIPT"; then
    mv /tmp/hp-omni10-buttons-init.sh.tmp "$INIT_SCRIPT"
    chmod 755 "$INIT_SCRIPT"
    BUTTONS_CHANGED=1
else
    rm -f /tmp/hp-omni10-buttons-init.sh.tmp
fi

# Run it immediately
bash "$INIT_SCRIPT" >/dev/null 2>&1 || true

# 5. Udev rule for button array input permissions and tags
UDEV_BUTTONS_RULE="/etc/udev/rules.d/99-omni10-buttons.rules"
cat <<'EOF' > /tmp/99-omni10-buttons.rules.tmp
# Ensure INTCFD9:00 button array events have input permissions and power-switch tag
SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="gpio-keys", KERNELS=="INTCFD9:00*", MODE="0660", GROUP="input", TAG+="power-switch"
EOF

if [ ! -f "$UDEV_BUTTONS_RULE" ] || ! cmp -s /tmp/99-omni10-buttons.rules.tmp "$UDEV_BUTTONS_RULE"; then
    mv /tmp/99-omni10-buttons.rules.tmp "$UDEV_BUTTONS_RULE"
    chmod 644 "$UDEV_BUTTONS_RULE"
    udevadm control --reload >/dev/null 2>&1 || true
    udevadm trigger -s input >/dev/null 2>&1 || true
    BUTTONS_CHANGED=1
else
    rm -f /tmp/99-omni10-buttons.rules.tmp
fi

# 6. Systemd boot service for early initialization
SERVICE_FILE="/etc/systemd/system/hp-omni10-buttons.service"
cat <<'EOF' > /tmp/hp-omni10-buttons.service.tmp
[Unit]
Description=HP Omni 10 Physical Buttons & Crystal Cove PMIC Initialization
After=systemd-modules-load.service
Before=nemo-headunit.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/hp-omni10-buttons-init.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

if [ ! -f "$SERVICE_FILE" ] || ! cmp -s /tmp/hp-omni10-buttons.service.tmp "$SERVICE_FILE"; then
    mv /tmp/hp-omni10-buttons.service.tmp "$SERVICE_FILE"
    chmod 644 "$SERVICE_FILE"
    BUTTONS_CHANGED=1
else
    rm -f /tmp/hp-omni10-buttons.service.tmp
fi

if command -v systemctl &>/dev/null; then
    if ! systemctl is-enabled hp-omni10-buttons.service &>/dev/null; then
        systemctl daemon-reload >/dev/null 2>&1 || true
        systemctl enable hp-omni10-buttons.service >/dev/null 2>&1 || true
        systemctl start hp-omni10-buttons.service >/dev/null 2>&1 || true
        BUTTONS_CHANGED=1
    fi
fi

if [ $BUTTONS_CHANGED -eq 1 ]; then
    echo -e "${GREEN}applicato (service + PMIC unmask configurati).${NC}"
else
    echo -e "${GREEN}già configurato.${NC}"
fi

# ---------------------------------------------------------------------------
# Fix 14: Broadcom Bluetooth SCO Audio Routing to HCI UART (HFP Voice Call Fix)
# ---------------------------------------------------------------------------
echo -n "  [hw-fix] Broadcom SCO routing to HCI UART (HFP voice call fix)... "
BCM_SCO_SCRIPT="/usr/local/bin/bcm-sco-routing.py"
BCM_SCO_SERVICE="/etc/systemd/system/bcm-sco-routing.service"
BCM_SCO_UDEV="/etc/udev/rules.d/99-bcm-sco-routing.rules"

TEMP_BCM_SCRIPT=$(mktemp)
cat <<'EOF' > "$TEMP_BCM_SCRIPT"
#!/usr/bin/env python3
"""
Sets Broadcom/Cypress Bluetooth SCO audio routing to HCI UART transport.
Broadcom controllers default to physical PCM pins, resulting in silent HFP audio.
Vendor Opcode 0xFC1C (Write_SCO_PCM_Int_Param) sets routing to Transport (0x01).
"""
import socket
import struct
import sys
import time

def apply_sco_routing(max_retries=15, delay=0.5):
    # HCI_COMMAND_PKT (0x01) + Opcode 0xFC1C + plen 5 + [0x01 (HCI Transport), 0x00, 0x00, 0x00, 0x00]
    cmd = struct.pack('<BHBBBBBB', 0x01, 0xFC1C, 5, 0x01, 0x00, 0x00, 0x00, 0x00)
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            s = socket.socket(31, socket.SOCK_RAW, 1)  # AF_BLUETOOTH, BTPROTO_HCI
            s.bind((0,))  # hci0
            s.send(cmd)
            s.close()
            print(f"[bcm-sco] Broadcom SCO routing -> HCI UART applied (attempt {attempt}).")
            return 0
        except Exception as e:
            last_err = e
            time.sleep(delay)
    print(f"[bcm-sco] Failed to set SCO routing after {max_retries} attempts: {last_err}", file=sys.stderr)
    return 1

if __name__ == "__main__":
    sys.exit(apply_sco_routing())
EOF

if [ ! -f "$BCM_SCO_SCRIPT" ] || ! cmp -s "$TEMP_BCM_SCRIPT" "$BCM_SCO_SCRIPT"; then
    mv "$TEMP_BCM_SCRIPT" "$BCM_SCO_SCRIPT"
    chmod 755 "$BCM_SCO_SCRIPT"
    BCM_SCO_CHANGED=1
else
    rm -f "$TEMP_BCM_SCRIPT"
fi

TEMP_BCM_SVC=$(mktemp)
cat <<'EOF' > "$TEMP_BCM_SVC"
[Unit]
Description=Broadcom Bluetooth SCO Audio Routing to HCI UART
After=bluetooth.service bluetooth-persistent-mac.service
Wants=bluetooth.service
Before=nemo-headunit.service nemo-kiosk.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/bin/bcm-sco-routing.py
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

if [ ! -f "$BCM_SCO_SERVICE" ] || ! cmp -s "$TEMP_BCM_SVC" "$BCM_SCO_SERVICE"; then
    mv "$TEMP_BCM_SVC" "$BCM_SCO_SERVICE"
    chmod 644 "$BCM_SCO_SERVICE"
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl enable bcm-sco-routing.service >/dev/null 2>&1 || true
    BCM_SCO_CHANGED=1
else
    rm -f "$TEMP_BCM_SVC"
fi

TEMP_BCM_UDEV=$(mktemp)
cat <<'EOF' > "$TEMP_BCM_UDEV"
# Trigger Broadcom SCO routing service via systemd whenever hci0 is initialized/added
ACTION=="add", SUBSYSTEM=="bluetooth", KERNEL=="hci0", TAG+="systemd", ENV{SYSTEMD_WANTS}+="bcm-sco-routing.service"
EOF

if [ ! -f "$BCM_SCO_UDEV" ] || ! cmp -s "$TEMP_BCM_UDEV" "$BCM_SCO_UDEV"; then
    mv "$TEMP_BCM_UDEV" "$BCM_SCO_UDEV"
    chmod 644 "$BCM_SCO_UDEV"
    udevadm control --reload >/dev/null 2>&1 || true
    BCM_SCO_CHANGED=1
else
    rm -f "$TEMP_BCM_UDEV"
fi

# Run immediately to ensure current session has SCO routing active
/usr/bin/python3 "$BCM_SCO_SCRIPT" >/dev/null 2>&1 || true

if [ $BCM_SCO_CHANGED -eq 1 ]; then
    echo -e "${GREEN}applicato (service + udev + helper configurati).${NC}"
else
    echo -e "${GREEN}già configurato.${NC}"
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
echo "    - Firmware Broadcom (BT/WiFi) e Intel SST DSP verificati in /lib/firmware/."
echo "    - Hardware Quirks generati in ${QUIRKS_FILE} (i965, scale 1.5, DMABuf caps)."
echo "    - PipeWire & WirePlumber abilitati all'avvio con user lingering attivo."
echo "    - Pulsanti volume fisico abilitati (PMIC unmask & hp-omni10-buttons.service)."
echo "    - Broadcom SCO audio routing verso HCI UART abilitato (bcm-sco-routing.service & udev)."

if [ $AUDIO_CHANGED -eq 1 ] || [ $GRUB_CHANGED -eq 1 ] || [ $SERVICES_CHANGED -eq 1 ] || [ $PKG_CHANGED -eq 1 ] || [ $DRACUT_CHANGED -eq 1 ] || [ $MKINIT_CHANGED -eq 1 ] || [ $GPU_CHANGED -eq 1 ] || [ $BT_MAC_CHANGED -eq 1 ] || [ $FW_CHANGED -eq 1 ] || [ $QUIRKS_CHANGED -eq 1 ] || [ $AUDIO_AUTOSTART_CHANGED -eq 1 ] || [ $BUTTONS_CHANGED -eq 1 ] || [ $BCM_SCO_CHANGED -eq 1 ]; then
    echo -e "  ${GREEN}[hw-fix] HP Omni10: fix applicati. Riavvio necessario.${NC}"
else
    echo -e "  ${GREEN}[hw-fix] HP Omni10: nessuna modifica necessaria.${NC}"
fi