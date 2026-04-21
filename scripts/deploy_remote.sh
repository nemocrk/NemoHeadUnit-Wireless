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
#   1. Syncs the repo to ~/NemoHeadUnit-Wireless on the remote machine
#   2. Installs system dependencies (apt)
#   3. Installs Miniconda if not present
#   4. Creates/updates the Conda environment from environment.yml
#   5. Initialises git submodules on the remote
#   6. Compiles .proto files via scripts/compile_protos.sh

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
# Step 1: Sync repo
# ---------------------------------------------------------------------------
echo "[1/5] Syncing repo to remote..."
rsync -avz --delete \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.venv' \
  --exclude='v2/protos/oaa' \
  "$REPO_ROOT/" "$REMOTE:$REMOTE_DIR/"
echo ""

# ---------------------------------------------------------------------------
# Step 2: System dependencies
# ---------------------------------------------------------------------------
echo "[2/5] Installing system dependencies..."
ssh "$REMOTE" bash <<'ENDSSH'
set -euo pipefail
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
  git \
  rsync \
  wget \
  curl \
  python3-dbus \
  libdbus-1-dev \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  gstreamer1.0-libav \
  libgstreamer1.0-dev \
  libgstreamer-plugins-base1.0-dev
echo "[OK] System dependencies installed."
ENDSSH
echo ""

# ---------------------------------------------------------------------------
# Step 3: Miniconda
# ---------------------------------------------------------------------------
echo "[3/5] Checking Miniconda on remote..."
ssh "$REMOTE" bash <<'ENDSSH'
set -euo pipefail
if command -v conda &>/dev/null; then
  echo "[OK] Conda already installed: $(conda --version)"
else
  echo "[INFO] Installing Miniconda..."
  ARCH=$(uname -m)
  MINICONDA_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-${ARCH}.sh"
  wget -q "$MINICONDA_URL" -O /tmp/miniconda.sh
  bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"
  rm /tmp/miniconda.sh
  eval "$($HOME/miniconda3/bin/conda shell.bash hook)"
  conda init bash
  echo "[OK] Miniconda installed."
fi
ENDSSH
echo ""

# ---------------------------------------------------------------------------
# Step 4: Conda environment
# ---------------------------------------------------------------------------
echo "[4/5] Creating/updating Conda environment (py314)..."
ssh "$REMOTE" bash <<'ENDSSH'
set -euo pipefail
eval "$(~/miniconda3/bin/conda shell.bash hook)"
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
# Step 5: Submodules + compile protos
# ---------------------------------------------------------------------------
echo "[5/5] Initialising submodules and compiling protos..."
ssh "$REMOTE" bash <<'ENDSSH'
set -euo pipefail
eval "$(~/miniconda3/bin/conda shell.bash hook)"
conda activate py314
cd ~/NemoHeadUnit-Wireless
git init
git submodule update --init --recursive
bash scripts/compile_protos.sh
echo "[OK] Protos compiled."
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
