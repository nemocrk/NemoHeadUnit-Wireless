#!/usr/bin/env bash
# deploy_remote.sh
# Deploys NemoHeadUnit-Wireless v2 to a remote Linux machine via SSH/rsync.
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
#   1. Syncs v2/ and environment.yml to ~/NemoHeadUnit-Wireless on the remote
#   2. Installs Miniconda if not present (no root required)
#   3. Creates/updates the Conda environment from environment.yml

set -euo pipefail

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
REMOTE_DIR="~/NemoHeadUnit-Wireless"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=================================================="
echo "  NemoHeadUnit-Wireless — Remote Deploy"
echo "  Target : $REMOTE:$REMOTE_DIR"
echo "=================================================="
echo ""

# ---------------------------------------------------------------------------
# Step 1: Sync v2/ + environment.yml
# ---------------------------------------------------------------------------
echo "[1/3] Syncing v2/ to remote..."
rsync -avz --delete \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  "$REPO_ROOT/v2/" "$REMOTE:$REMOTE_DIR/v2/"

echo "[1/3] Syncing environment.yml to remote..."
rsync -avz \
  "$REPO_ROOT/environment.yml" "$REMOTE:$REMOTE_DIR/environment.yml"
echo ""

# ---------------------------------------------------------------------------
# Step 2: Miniconda (no root)
# ---------------------------------------------------------------------------
echo "[2/3] Checking Miniconda on remote..."
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
# Step 3: Conda environment
# ---------------------------------------------------------------------------
echo "[3/3] Creating/updating Conda environment (py314)..."
ssh "$REMOTE" bash <<'ENDSSH'
set -euo pipefail
eval "$($HOME/miniconda3/bin/conda shell.bash hook)"
cd ~/NemoHeadUnit-Wireless
if conda env list | grep -q '^py314'; then
  echo "[INFO] Environment exists, updating..."
  conda env update -f environment.yml --prune
else
  echo "[INFO] Creating environment..."
  conda env create -f environment.yml
fi
echo "[OK] Conda environment ready."
ENDSSH
echo ""

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo "=================================================="
echo "  Deploy completato!"
echo ""
echo "  Per avviare v2 sulla macchina remota:"
echo "    ssh $REMOTE"
echo "    cd ~/NemoHeadUnit-Wireless/v2"
echo "    conda activate py314"
echo "    python main.py"
echo "=================================================="
