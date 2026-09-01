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
    # Add intel_idle.max_cstate=1 and i915.enable_rc6=0 if missing
    if ! grep -q "intel_idle.max_cstate=1" "$GRUB_FILE"; then
        sed -i 's/^\(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*\)"/\1 intel_idle.max_cstate=1"/' "$GRUB_FILE"
        sed -i "s/^\(GRUB_CMDLINE_LINUX_DEFAULT='[^']*\)'/\1 intel_idle.max_cstate=1'/" "$GRUB_FILE"
        GRUB_CHANGED=1
    fi

    if ! grep -q "i915.enable_rc6=0" "$GRUB_FILE"; then
        sed -i 's/^\(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*\)"/\1 i915.enable_rc6=0"/' "$GRUB_FILE"
        sed -i "s/^\(GRUB_CMDLINE_LINUX_DEFAULT='[^']*\)'/\1 i915.enable_rc6=0'/" "$GRUB_FILE"
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
            local grub_cfg="/boot/grub/grub.cfg"
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
# Riepilogo
# ---------------------------------------------------------------------------
echo ""
echo -e "  [hw-fix] Info:"
echo "    - bluetooth / wpa_supplicant / org.nemo.APManager : Mantenuti attivi."
echo "    - cups.service è stato disabilitato per performance."

if [ $AUDIO_CHANGED -eq 1 ] || [ $GRUB_CHANGED -eq 1 ] || [ $SERVICES_CHANGED -eq 1 ] || [ $PKG_CHANGED -eq 1 ] || [ $DRACUT_CHANGED -eq 1 ]; then
    echo -e "  ${GREEN}[hw-fix] HP Omni10: fix applicati. Riavvio necessario.${NC}"
else
    echo -e "  ${GREEN}[hw-fix] HP Omni10: nessuna modifica necessaria.${NC}"
fi