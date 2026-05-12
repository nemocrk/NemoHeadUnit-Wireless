#!/bin/bash

# Definisco i colori per rendere l'output più leggibile
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # Nessun colore

echo -e "${CYAN}====================================================${NC}"
echo -e "${YELLOW} Ottimizzazione HP OMNI 10 (Intel Bay Trail)        ${NC}"
echo -e "${CYAN}====================================================${NC}"

# Variabili di stato
AUDIO_CHANGED=0
GRUB_CHANGED=0

# --- 1. FIX AUDIO ---
# Usa un file di configurazione dedicato per maggiore pulizia
AUDIO_CONF="/etc/modprobe.d/baytrail-audio-fix.conf"

echo -n "Controllo fix per il loop audio... "

# Controllo se le stringhe esistono già nel file
if ! grep -q "options snd_sof sof_debug=1" "$AUDIO_CONF" 2>/dev/null; then
    echo "options snd_sof sof_debug=1" | sudo tee -a "$AUDIO_CONF" > /dev/null
    AUDIO_CHANGED=1
fi

if ! grep -q "options snd-intel-dspcfg dsp_driver=2" "$AUDIO_CONF" 2>/dev/null; then
    echo "options snd-intel-dspcfg dsp_driver=2" | sudo tee -a "$AUDIO_CONF" > /dev/null
    AUDIO_CHANGED=1
fi

if[ $AUDIO_CHANGED -eq 1 ]; then
    echo -e "${GREEN}Applicato.${NC}"
else
    echo -e "${GREEN}Già presente.${NC}"
fi

# --- 2. FIX FREEZE DI SISTEMA (C-States) ---
GRUB_FILE="/etc/default/grub"

echo -n "Controllo fix per il freeze di sistema (C-States)... "

if [ -f "$GRUB_FILE" ]; then
    # Controllo se il parametro è già presente nel file grub
    if ! grep -q "intel_idle.max_cstate=1" "$GRUB_FILE"; then
        # Aggiunge il parametro all'interno delle doppie virgolette della riga GRUB_CMDLINE_LINUX_DEFAULT
        sudo sed -i 's/^\(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*\)"/\1 intel_idle.max_cstate=1"/' "$GRUB_FILE"
        GRUB_CHANGED=1
        echo -e "${GREEN}Applicato.${NC}"
    else
        echo -e "${GREEN}Già presente.${NC}"
    fi
else
    echo -e "${YELLOW}File di configurazione GRUB non trovato. Ignorato.${NC}"
fi

# --- 3. AGGIORNAMENTO GRUB ---
# Aggiorniamo GRUB solo se è stata fatta una modifica al file
if [ $GRUB_CHANGED -eq 1 ]; then
    echo -e "${CYAN}Aggiornamento della configurazione di GRUB in corso...${NC}"
    sudo update-grub
fi

echo -e "${CYAN}====================================================${NC}"
# --- RIASSUNTO FINALE ---
if [ $AUDIO_CHANGED -eq 1 ] ||[ $GRUB_CHANGED -eq 1 ]; then
    echo -e "${GREEN}Tutte le modifiche sono state applicate con successo!${NC}"
    echo -e "${YELLOW}Riavvia il computer per rendere effettivi i cambiamenti.${NC}"
else
    echo -e "${GREEN}Il sistema è già ottimizzato. Nessuna modifica effettuata.${NC}"
fi