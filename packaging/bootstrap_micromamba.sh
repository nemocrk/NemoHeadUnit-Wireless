#!/usr/bin/env bash
# bootstrap_micromamba.sh — Installa micromamba se non è disponibile.
#
# Chiamato da postinst prima di 'micromamba env create'.
# Idempotente: esce subito se micromamba è già nel PATH o in /opt/micromamba.
#
# Deve essere eseguito come root.

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

MICROMAMBA_INSTALL_DIR="/opt/micromamba"
MICROMAMBA_BIN="${MICROMAMBA_INSTALL_DIR}/bin/micromamba"

# ---------------------------------------------------------------------------
# Controlla se micromamba è già disponibile
# ---------------------------------------------------------------------------
if command -v micromamba &>/dev/null; then
    echo -e "${GREEN}[bootstrap_micromamba] micromamba già presente: $(command -v micromamba)${NC}"
    exit 0
fi

if [ -x "${MICROMAMBA_BIN}" ]; then
    echo -e "${GREEN}[bootstrap_micromamba] micromamba trovato in ${MICROMAMBA_INSTALL_DIR} — aggiungo al PATH.${NC}"
    export PATH="${MICROMAMBA_INSTALL_DIR}/bin:${PATH}"
    exit 0
fi

# ---------------------------------------------------------------------------
# Scarica e installa micromamba
# ---------------------------------------------------------------------------
echo -e "${CYAN}[bootstrap_micromamba] micromamba non trovato — installo in ${MICROMAMBA_INSTALL_DIR}...${NC}"

if ! command -v curl &>/dev/null && ! command -v wget &>/dev/null; then
    echo -e "${RED}[bootstrap_micromamba] ERROR: né curl né wget disponibili. Installa uno dei due.${NC}" >&2
    exit 1
fi

mkdir -p "${MICROMAMBA_INSTALL_DIR}"
echo -e "${CYAN}[bootstrap_micromamba] Download da https://micro.mamba.pm/api/micromamba/$(uname)-$(uname -m)/latest${NC}"

if command -v curl &>/dev/null; then
    curl -Ls "https://micro.mamba.pm/api/micromamba/$(uname)-$(uname -m)/latest" | tar -xvj -C "${MICROMAMBA_INSTALL_DIR}/" bin/micromamba --strip-components=1
else
    wget -qO- "https://micro.mamba.pm/api/micromamba/$(uname)-$(uname -m)/latest" | tar -xvj -C "${MICROMAMBA_INSTALL_DIR}/" bin/micromamba --strip-components=1
fi

chmod +x "${MICROMAMBA_INSTALL_DIR}/micromamba"

# Inizializzazione base (opzionale, ma utile per definire i canali di default)
"${MICROMAMBA_INSTALL_DIR}/micromamba" shell init -s bash --root-prefix "${MICROMAMBA_INSTALL_DIR}"

export PATH="${MICROMAMBA_INSTALL_DIR}:${PATH}"

echo -e "${GREEN}[bootstrap_micromamba] Micromamba installato in ${MICROMAMBA_INSTALL_DIR}${NC}"
echo -e "${YELLOW}[bootstrap_micromamba] Per usare micromamba nelle shell utente aggiungi a ~/.bashrc:${NC}"
echo -e "${YELLOW}  export PATH=${MICROMAMBA_INSTALL_DIR}:${PATH}${NC}"