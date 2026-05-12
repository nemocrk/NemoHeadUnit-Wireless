#!/usr/bin/env bash
# run_hardware_fixes.sh — Generic hardware fix runner
#
# Legge registry.conf nella stessa directory e per ogni regola:
#   1. Esegue detect_cmd  — se exit 0, la piattaforma è riconosciuta
#   2. Esegue fix_script  — path relativo alla directory di questo script
#
# Uso:
#   sudo bash packaging/hardware_fixes/run_hardware_fixes.sh
#
# Chiamato automaticamente da postinst (deb) e da install.sh (manuale).
# Deve essere eseguito come root.

set -euo pipefail

YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

FIX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${FIX_DIR}/registry.conf"

if [ ! -f "$REGISTRY" ]; then
    echo -e "${YELLOW}[hw-fix] registry.conf non trovato in ${FIX_DIR} — nessun fix applicato.${NC}"
    exit 0
fi

echo -e "${CYAN}[hw-fix] Rilevamento piattaforma hardware...${NC}"

MATCH_COUNT=0

while IFS='|' read -r detect_cmd fix_script; do
    # Salta righe vuote e commenti
    detect_cmd="$(echo "$detect_cmd" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    fix_script="$(echo "$fix_script" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -z "$detect_cmd" || "$detect_cmd" == \#* ]] && continue
    [[ -z "$fix_script" ]] && continue

    # Esegui detect_cmd
    if bash -c "$detect_cmd" &>/dev/null; then
        MATCH_COUNT=$((MATCH_COUNT + 1))
        FIX_PATH="${FIX_DIR}/${fix_script}"

        echo -e "${CYAN}[hw-fix] Piattaforma riconosciuta — esecuzione: ${fix_script}${NC}"

        if [ ! -f "$FIX_PATH" ]; then
            echo -e "${RED}[hw-fix] ERROR: fix script non trovato: ${FIX_PATH}${NC}"
            continue
        fi

        chmod +x "$FIX_PATH"
        bash "$FIX_PATH"
    fi
done < <(grep -v '^[[:space:]]*#' "$REGISTRY" | grep -v '^[[:space:]]*$')

if [ $MATCH_COUNT -eq 0 ]; then
    echo -e "${CYAN}[hw-fix] Nessuna piattaforma specifica rilevata — nessun fix necessario.${NC}"
fi
