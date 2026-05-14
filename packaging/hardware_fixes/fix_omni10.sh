#!/usr/bin/env bash
# fix_omni10.sh — HP Omni10 / Intel Bay Trail platform fixes
#
# Fix 1: Audio loop (SOF debug + DSP driver override)
# Fix 2: System freeze (intel_idle C-States cap via GRUB)
#
# Deve essere eseguito come root.
# Idempotente: controlla prima di modificare.

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}  [hw-fix] HP Omni10 / Bay Trail fixes${NC}"

AUDIO_CHANGED=0
GRUB_CHANGED=0

# ---------------------------------------------------------------------------
# Fix 1: Audio loop
# ---------------------------------------------------------------------------
AUDIO_CONF="/etc/modprobe.d/baytrail-audio-fix.conf"

echo -n "  [hw-fix] Audio loop fix... "

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
# Fix 2: System freeze — C-States cap via GRUB
# ---------------------------------------------------------------------------
GRUB_FILE="/etc/default/grub"

echo -n "  [hw-fix] C-States freeze fix (GRUB)... "

if [ -f "$GRUB_FILE" ]; then
    if ! grep -q "intel_idle.max_cstate=1" "$GRUB_FILE"; then
        sed -i 's/^\(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*\)"/\1 intel_idle.max_cstate=1"/' "$GRUB_FILE"
        GRUB_CHANGED=1
    fi

    if [ $GRUB_CHANGED -eq 1 ]; then
        echo -e "${GREEN}applicato.${NC}"
        echo -n "  [hw-fix] Aggiornamento GRUB... "
        if command -v update-grub &>/dev/null; then
            update-grub
            echo -e "${GREEN}OK.${NC}"
        else
            echo -e "${YELLOW}WARNING: update-grub non trovato (systemd-boot?).${NC}"
            echo -e "${YELLOW}         Aggiorna manualmente il bootloader per applicare i parametri kernel.${NC}"
        fi
    else
        echo -e "${GREEN}già presente.${NC}"
    fi
else
    echo -e "${YELLOW}WARNING: $GRUB_FILE non trovato — fix C-States saltato.${NC}"
fi

# ---------------------------------------------------------------------------
# Riepilogo
# ---------------------------------------------------------------------------
echo ""
if [ $AUDIO_CHANGED -eq 1 ] || [ $GRUB_CHANGED -eq 1 ]; then
    echo -e "  ${GREEN}[hw-fix] HP Omni10: fix applicati. Riavvio necessario.${NC}"
else
    echo -e "  ${GREEN}[hw-fix] HP Omni10: nessuna modifica necessaria.${NC}"
fi

sudo apt update && sudo apt install i965-va-driver vainfo