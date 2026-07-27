#!/usr/bin/env bash
# deploy_remote.sh
# Deploys NemoHeadUnit-Wireless to a remote Linux machine via SSH/rsync,
# then avvia automaticamente main.py con log rotation e output live.
#
# Usage:
#   bash scripts/deploy_remote.sh [--sync-env] [--omni-fix] <user> <host>
#
# Example:
#   bash scripts/deploy_remote.sh --sync-env --omni-fix hpuser 192.168.1.50
#   bash scripts/deploy_remote.sh --sync-env pi 192.168.1.42

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
KEEP=5
LOGFILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/logs/deploy.log"
REMOTE_DIR="NemoHeadUnit-Wireless"
SYNC_ENV_FLAG=0
OMNI_FIX_FLAG=0
REMOTE_USER=""
REMOTE_HOST=""

# ---------------------------------------------------------------------------
# Args Parsing
# ---------------------------------------------------------------------------
show_help() {
  echo "Usage: $0 [--sync-env] [--omni-fix] <user> <host>"
  echo ""
  echo "Options:"
  echo "  --sync-env    Sincronizza environment.yml e configura Conda"
  echo "  --omni-fix    Applica ottimizzazioni audio/C-States per HP OMNI 10 (Intel Bay Trail)"
  echo "  --help        Mostra questo messaggio"
  echo ""
  echo "Example:"
  echo "  $0 --sync-env --omni-fix hpuser 192.168.1.50"
  exit 0
}

# Parsing robusto degli argomenti
while [[ $# -gt 0 ]]; do
  case $1 in
    --sync-env)
      SYNC_ENV_FLAG=1
      shift
      ;;
    --omni-fix)
      OMNI_FIX_FLAG=1
      shift
      ;;
    --help|-h)
      show_help
      ;;
    *)
      if [ -z "$REMOTE_USER" ]; then
        REMOTE_USER="$1"
      elif [ -z "$REMOTE_HOST" ]; then
        REMOTE_HOST="$1"
      else
        echo "Errore: Troppi argomenti posizionali."
        show_help
      fi
      shift
      ;;
  esac
done

if [ -z "$REMOTE_USER" ] || [ -z "$REMOTE_HOST" ]; then
  echo "Errore: Mancano utente o host."
  show_help
fi

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
echo "[1/6] Preparing remote directory..."
ssh "$REMOTE" "mkdir -p /home/$REMOTE_USER/$REMOTE_DIR"
echo "[OK] Remote directory ready."
echo ""

# ---------------------------------------------------------------------------
# Step 2: Ottimizzazioni HP OMNI 10 (Bay Trail) - OPZIONALE
# ---------------------------------------------------------------------------
if [ "$OMNI_FIX_FLAG" = "1" ]; then
  echo "[2/6] Applicazione ottimizzazioni per HP OMNI 10..."
  
  # Scriviamo lo script temporaneo sul server remoto tramite heredoc
  ssh "$REMOTE" "cat > /tmp/omni_fix.sh" << 'EOF'
#!/bin/bash
AUDIO_CHANGED=0
GRUB_CHANGED=0
AUDIO_CONF="/etc/modprobe.d/baytrail-audio-fix.conf"
GRUB_FILE="/etc/default/grub"

echo "Controllo fix per il loop audio..."
if ! grep -q "options snd_sof sof_debug=1" "$AUDIO_CONF" 2>/dev/null; then
    echo "options snd_sof sof_debug=1" | sudo tee -a "$AUDIO_CONF" > /dev/null
    AUDIO_CHANGED=1
fi
if ! grep -q "options snd-intel-dspcfg dsp_driver=2" "$AUDIO_CONF" 2>/dev/null; then
    echo "options snd-intel-dspcfg dsp_driver=2" | sudo tee -a "$AUDIO_CONF" > /dev/null
    AUDIO_CHANGED=1
fi

echo "Controllo fix per il freeze di sistema (C-States)..."
if [ -f "$GRUB_FILE" ]; then
    if ! grep -q "intel_idle.max_cstate=1" "$GRUB_FILE"; then
        sudo sed -i 's/^\(GRUB_CMDLINE_LINUX_DEFAULT="[^"]*\)"/\1 intel_idle.max_cstate=1"/' "$GRUB_FILE"
        GRUB_CHANGED=1
    fi
fi

if [ $GRUB_CHANGED -eq 1 ]; then
    echo "Aggiornamento di GRUB in corso..."
    sudo update-grub
fi

if [ $AUDIO_CHANGED -eq 1 ] ||[ $GRUB_CHANGED -eq 1 ]; then
    echo "[!] Modifiche di sistema applicate. Sarà necessario un riavvio in seguito."
else
    echo "[OK] Sistema HP OMNI 10 già ottimizzato."
fi
EOF

  # Eseguiamo lo script remoto allocando un TTY (-t) così sudo può chiedere la password se serve
  ssh -t "$REMOTE" "bash /tmp/omni_fix.sh && rm /tmp/omni_fix.sh"
  echo ""
else
  echo "[2/6] Ottimizzazioni hardware ignorate (usa --omni-fix per abilitare)."
  echo ""
fi

# ---------------------------------------------------------------------------
# Step 3: Sync source + environment.yml
# ---------------------------------------------------------------------------
echo "[3/6] Syncing source to remote..."
rsync -avz --delete \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.git' \
  --exclude='tests' \
  --exclude='build' \
  --exclude='dist' \
  -e ssh \
  "$REPO_ROOT/" "$REMOTE:/home/$REMOTE_USER/$REMOTE_DIR/"
echo ""

if [ "$SYNC_ENV_FLAG" = "1" ]; then
  echo "[3.5/6] Syncing environment.yml to remote..."
  rsync -avz \
    -e ssh \
    "$REPO_ROOT/environment.yml" "$REMOTE:/home/$REMOTE_USER/$REMOTE_DIR/environment.yml"
  echo ""

# ---------------------------------------------------------------------------
# Step 4: Miniconda + Libmamba Solver (Speed boost)
# ---------------------------------------------------------------------------
  echo "[4/6] Checking Miniconda and Solver on remote..."
  ssh "$REMOTE" bash <<'ENDSSH'
  set -euo pipefail
  CONDA_BIN="$HOME/miniconda3/bin/conda"
  
  # 1. Installazione Miniconda (se manca)
  if ! command -v conda &>/dev/null && [ ! -x "$CONDA_BIN" ]; then
    echo "[INFO] Installing Miniconda..."
    ARCH=$(uname -m)
    wget -q "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-${ARCH}.sh" -O /tmp/miniconda.sh
    bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"
    rm /tmp/miniconda.sh
    "$CONDA_BIN" init bash
  fi

  eval "$($HOME/miniconda3/bin/conda shell.bash hook)"

  # 2. Installazione libmamba-solver (se manca)
  if ! conda list -n base conda-libmamba-solver | grep -q "conda-libmamba-solver"; then
    echo "[INFO] Installing libmamba solver..."
    conda install -n base conda-libmamba-solver -y --quiet
  else
    echo "[OK] libmamba solver already installed."
  fi

  # 3. Configurazione Solver e Canali (solo se diversi)
  CURRENT_SOLVER=$(conda config --show solver --json | grep '"solver":' | cut -d'"' -f4 || echo "classic")
  if [ "$CURRENT_SOLVER" != "libmamba" ]; then
    echo "[INFO] Setting libmamba as default solver..."
    conda config --set solver libmamba
    conda config --set max_parallel_downloads 10
    conda config --set connect_timeout 10
  fi

  # 4. Configurazione Canali (rimuove defaults, aggiunge conda-forge)
  if conda config --show channels | grep -q "defaults"; then
    echo "[INFO] Cleaning channels (removing defaults, adding conda-forge)..."
    conda config --remove channels defaults || true
    conda config --add channels conda-forge
    conda config --set channel_priority strict
    # Pulisce gli indici solo se cambiano i canali, per evitare overhead
    conda clean -i -y
  fi

  echo "[OK] Conda environment logic ready."
ENDSSH

  echo ""


# ---------------------------------------------------------------------------
# Step 5: Conda environment + avvio
# ---------------------------------------------------------------------------
  echo "[5/6] Creating/updating Conda environment (py314)..."
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
else
  echo "[4/6 & 5/6] Conda env setup ignorato (usa --sync-env per abilitare)."
  echo ""
fi

# ---------------------------------------------------------------------------
# Step 6: Avvio automatico main.py (output live + tee log remoto)
# ---------------------------------------------------------------------------
echo "[6/6] Avvio main.py sulla macchina remota..."
echo "      (Ctrl+C per interrompere — il log rimane in $LOGFILE)"
echo ""
exec ssh -t "$REMOTE" '
  source ~/miniconda3/etc/profile.d/conda.sh &&
  conda activate py314 &&
  cd ~/NemoHeadUnit-Wireless &&
  export DEBUG=1 DISPLAY=:0 DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket &&
  
  # 1. Start Python fully in the background, safe from disconnections
  nohup python -m main > ~/NemoHeadUnit-Wireless/deploy_remote.log 2>&1 &
  PYTHON_PID=$!
  
  # 2. Catch Ctrl+C locally and explicitly forward it to Python
  trap "echo -e \"\n[SSH] Caught Ctrl+C! Forwarding gracefully to Python (PID: $PYTHON_PID)...\"; kill -INT $PYTHON_PID" INT
  
  # 3. Stream the logs live. The --pid flag tells tail to exit automatically when Python stops!
  tail --pid=$PYTHON_PID -n 0 -f ~/NemoHeadUnit-Wireless/deploy_remote.log &
  
  # 4. Wait for Python to finish. If we press Ctrl+C, wait is interrupted, 
  # the trap fires, and the loop ensures we resume waiting for graceful shutdown.
  while kill -0 $PYTHON_PID 2>/dev/null; do
      wait $PYTHON_PID
  done
'