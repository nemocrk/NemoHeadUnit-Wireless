#!/usr/bin/env bash
# deploy_remote.sh
# Deploys NemoHeadUnit-Wireless v2 to a remote Linux machine via SSH/rsync,
# then avvia automaticamente main.py con log rotation e output live.
#
# Usage:
#   bash scripts/deploy_remote.sh <user> <host>
#
# Example:
#   bash scripts/deploy_remote.sh pi 192.168.1.42
#
# Requirements (local):
#   - ssh access configured (key-based recommended)
#   - rsync installed locally
#
# What this script does:
#   1. Ruota i log locali (deploy.log, keep ultimi 5)
#   2. Crea la directory remota se non esiste
#   3. Syncs v2/ and environment.yml to ~/NemoHeadUnit-Wireless on the remote
#   4. Installs Miniconda if not present (no root required)
#   5. Creates/updates the Conda environment from environment.yml
#   6. Avvia main.py via SSH con output live + tee su log

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
KEEP=5
LOGFILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/logs/deploy.log"
REMOTE_DIR="NemoHeadUnit-Wireless"

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
if [ $# -lt 2 ]; then
  echo "Usage: $0 <user> <host>"
  exit 1
fi

REMOTE_USER="$1"
REMOTE_HOST="$2"
REMOTE="$REMOTE_USER@$REMOTE_HOST"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---------------------------------------------------------------------------
# Log rotation
# ---------------------------------------------------------------------------
LOGDIR="$(dirname "$LOGFILE")"
BASE="$(basename "$LOGFILE")"
mkdir -p "$LOGDIR"

for (( i=KEEP; i>1; i-- )); do
  prev=$((i-1))
  src="$LOGDIR/$BASE.$prev"
  dst="$LOGDIR/$BASE.$i"
  [ -e "$src" ] && mv -f "$src" "$dst"
done

[ -e "$LOGFILE" ] && mv -f "$LOGFILE" "$LOGDIR/$BASE.1"
: > "$LOGFILE"

# Tutto l'output da qui in poi va sia a terminale che al log
exec > >(tee -a "$LOGFILE") 2>&1

echo "=================================================="
echo "  NemoHeadUnit-Wireless — Remote Deploy"
echo "  Target : $REMOTE:~/$REMOTE_DIR"
echo "  Log    : $LOGFILE"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================================="
echo ""

# ---------------------------------------------------------------------------
# Step 1: Crea directory remota
# ---------------------------------------------------------------------------
echo "[1/5] Preparing remote directory..."
ssh "$REMOTE" "mkdir -p /home/$REMOTE_USER/$REMOTE_DIR/v2"
echo "[OK] Remote directory ready."
echo ""

# ---------------------------------------------------------------------------
# Step 2: Sync v2/ + environment.yml
# ---------------------------------------------------------------------------
echo "[2/5] Syncing v2/ to remote..."
rsync -avz --delete \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  -e ssh \
  "$REPO_ROOT/v2/" "$REMOTE:/home/$REMOTE_USER/$REMOTE_DIR/v2/"

echo "[2/5] Syncing environment.yml to remote..."
rsync -avz \
  -e ssh \
  "$REPO_ROOT/environment.yml" "$REMOTE:/home/$REMOTE_USER/$REMOTE_DIR/environment.yml"
echo ""

# ---------------------------------------------------------------------------
# Step 3: Miniconda (no root)
# ---------------------------------------------------------------------------
echo "[3/5] Checking Miniconda on remote..."
ssh "$REMOTE" bash <<'ENDSSH'
set -euo pipefail
if command -v conda &>/dev/null || [ -x "$HOME/miniconda3/bin/conda" ]; then
  echo "[OK] Conda already installed."
else
  echo "[INFO] Installing Miniconda (no root)..."
  ARCH=$(uname -m)
  MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-${ARCH}.sh"
  wget -q "$MINICONDA_URL" -O /tmp/miniconda.sh
  bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"
  rm /tmp/miniconda.sh
  "$HOME/miniconda3/bin/conda" init bash
  echo "[OK] Miniconda installed."
fi
ENDSSH
echo ""

# ---------------------------------------------------------------------------
# Step 4: Conda environment + avvio
# ---------------------------------------------------------------------------
#echo "[4/5] Creating/updating Conda environment (py314)..."
#ssh "$REMOTE" bash <<'ENDSSH'
#set -euo pipefail
#eval "$($HOME/miniconda3/bin/conda shell.bash hook)"
#cd ~/NemoHeadUnit-Wireless
#if conda env list | grep -q '^py314'; then
#  echo "[INFO] Environment exists, updating..."
#  conda env update -f environment.yml --prune
#else
#  echo "[INFO] Creating environment..."
#  conda env create -f environment.yml
#fi
#echo "[OK] Conda environment ready."
#ENDSSH
#echo ""

# ---------------------------------------------------------------------------
# Step 5: Avvio automatico main.py (output live + tee log remoto)
# ---------------------------------------------------------------------------
echo "[5/5] Avvio main.py sulla macchina remota..."
echo "      (Ctrl+C per interrompere — il log rimane in $LOGFILE)"
echo ""
exec ssh -t "$REMOTE" \
  "source ~/miniconda3/etc/profile.d/conda.sh && \
   conda activate py314 && \
   cd ~/NemoHeadUnit-Wireless/v2 && \
   DISPLAY=:0 DBUS_SYSTEM_BUS_ADDRESS=\"unix:path=/run/dbus/system_bus_socket\" python -m main"
