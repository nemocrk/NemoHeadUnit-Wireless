#!/usr/bin/env bash
# bootstrap_conda.sh — Installa Miniconda se conda non è disponibile.
#
# Chiamato da postinst prima di 'conda env create'.
# Idempotente: esce subito se conda è già nel PATH o in /opt/miniconda3.
#
# Deve essere eseguito come root.

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

MINICONDA_INSTALL_DIR="/opt/miniconda3"
MINICONDA_SH="/tmp/miniconda_installer.sh"

# Architettura
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)  MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh" ;;
    aarch64) MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh" ;;
    *) echo -e "${RED}[bootstrap_conda] Architettura non supportata: ${ARCH}${NC}" >&2; exit 1 ;;
esac

# ---------------------------------------------------------------------------
# Controlla se conda è già disponibile
# ---------------------------------------------------------------------------
if command -v conda &>/dev/null; then
    echo -e "${GREEN}[bootstrap_conda] conda già presente: $(command -v conda)${NC}"
    exit 0
fi

if [ -x "${MINICONDA_INSTALL_DIR}/bin/conda" ]; then
    echo -e "${GREEN}[bootstrap_conda] Miniconda trovato in ${MINICONDA_INSTALL_DIR} — aggiungo al PATH.${NC}"
    export PATH="${MINICONDA_INSTALL_DIR}/bin:${PATH}"
    exit 0
fi

# ---------------------------------------------------------------------------
# Scarica e installa Miniconda
# ---------------------------------------------------------------------------
echo -e "${CYAN}[bootstrap_conda] conda non trovato — installo Miniconda in ${MINICONDA_INSTALL_DIR}...${NC}"

if ! command -v curl &>/dev/null && ! command -v wget &>/dev/null; then
    echo -e "${RED}[bootstrap_conda] ERROR: né curl né wget disponibili. Installa uno dei due.${NC}" >&2
    exit 1
fi

echo -e "${CYAN}[bootstrap_conda] Download da ${MINICONDA_URL}${NC}"
if command -v curl &>/dev/null; then
    curl -fsSL "${MINICONDA_URL}" -o "${MINICONDA_SH}"
else
    wget -q "${MINICONDA_URL}" -O "${MINICONDA_SH}"
fi

chmod +x "${MINICONDA_SH}"
bash "${MINICONDA_SH}" -b -p "${MINICONDA_INSTALL_DIR}"
rm -f "${MINICONDA_SH}"

export PATH="${MINICONDA_INSTALL_DIR}/bin:${PATH}"

# Inizializza conda per la shell corrente (non modifica .bashrc)
# shellcheck source=/dev/null
source "${MINICONDA_INSTALL_DIR}/etc/profile.d/conda.sh"

echo -e "${GREEN}[bootstrap_conda] Miniconda installato in ${MINICONDA_INSTALL_DIR}${NC}"
echo -e "${YELLOW}[bootstrap_conda] Per usare conda nelle shell utente aggiungi a ~/.bashrc:${NC}"
echo -e "${YELLOW}  export PATH=${MINICONDA_INSTALL_DIR}/bin:\$PATH${NC}"
