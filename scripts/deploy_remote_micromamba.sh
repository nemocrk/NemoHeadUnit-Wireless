#!/usr/bin/env bash
# deploy_remote_micromamba.sh
# Deploys NemoHeadUnit-Wireless to a remote Linux machine via SSH/rsync using micromamba,
# then avvia automaticamente main.py con log rotation e output live.
#
# Usage:
#   bash scripts/deploy_remote_micromamba.sh [--sync-env] [--omni-fix] <user> <host>
#
# Example:
#   bash scripts/deploy_remote_micromamba.sh --sync-env --omni-fix hpuser 192.168.1.50
#   bash scripts/deploy_remote_micromamba.sh --sync-env pi 192.168.1.42

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
KEEP=5
LOGFILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/logs/deploy_micromamba.log"
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
  echo "  --sync-env    Sincronizza environment.yml e configura Micromamba"
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
echo "  NemoHeadUnit-Wireless — Remote Deploy (Micromamba)"
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
  echo "[2/6] Ottimizzazioni hardware ignorate (usa --omni-fix per abilitarle)."
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
# Step 4: Micromamba setup
# ---------------------------------------------------------------------------
  echo "[4/6] Checking Micromamba on remote..."
  ssh "$REMOTE" bash <<'ENDSSH'
  set -euo pipefail
  MM_BIN="$HOME/bin/micromamba"
  export MAMBA_ROOT_PREFIX="$HOME/micromamba"
  
  # 1. Installazione Micromamba (se manca)
  if ! command -v micromamba &>/dev/null && [ ! -f "$MM_BIN" ]; then
    echo "[INFO] Installing micromamba..."
    mkdir -p "$HOME/bin"
    curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xvj -C "$HOME/bin/" --strip-components=1 bin/micromamba
    chmod +x "$MM_BIN"
  fi
  
  # Attiva Micromamba al volo per questa sessione di script
  eval "$("$MM_BIN" shell hook --shell bash)"
  mkdir -p "$MAMBA_ROOT_PREFIX"
  
  # 2. Configurazione Canali (evita duplicati ed errori)
  echo "[INFO] Configuring channels..."
  if ! micromamba config get channels | grep -q "conda-forge"; then
    micromamba config add channels conda-forge
  fi
  
  echo "[OK] Micromamba ready."
ENDSSH


  echo ""


# ---------------------------------------------------------------------------
# Step 5: Micromamba environment + avvio
# ---------------------------------------------------------------------------
  echo "[5/6] Creating/updating Micromamba environment (py314)..."
  ssh "$REMOTE" bash <<'ENDSSH'
  set -euo pipefail
  export MAMBA_ROOT_PREFIX="$HOME/micromamba"
  MM_BIN="$HOME/bin/micromamba"
  cd ~/NemoHeadUnit-Wireless

  if $MM_BIN env list | grep -q '^py314$'; then
    echo "[INFO] Environment exists, updating..."
    $MM_BIN env update -y -n py314 -f environment.yml
  else
    echo "[INFO] Creating environment..."
    $MM_BIN create -y -n py314 -f environment.yml
  fi
  echo "[OK] Micromamba environment ready."
ENDSSH
  echo ""
else
  echo "[4/6 & 5/6] Micromamba env setup ignorato (usa --sync-env per abilitarlo)."
  echo ""
fi

# ---------------------------------------------------------------------------
# Step 6: Avvio automatico main.py (output live + tee log remoto)
# ---------------------------------------------------------------------------
echo "[6/6] Avvio main.py sulla macchina remota..."
echo "      (Ctrl+C per interrompere — il log rimane in $LOGFILE)"
echo ""
exec ssh -t "$REMOTE" '
  export MAMBA_ROOT_PREFIX="$HOME/micromamba"
  MM_BIN="$HOME/bin/micromamba"
  cd ~/NemoHeadUnit-Wireless &&
  export DEBUG=1 DISPLAY=:0 DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket &&
  
  # Usa micromamba run per eseguire il comando nell''ambiente corretto senza attivazione complessa
  nohup $MM_BIN run -n py314 python -m main > ~/NemoHeadUnit-Wireless/deploy_remote_micromamba.log 2>&1 &
  PYTHON_PID=$!
  
  # 2. Catch Ctrl+C locally and explicitly forward to Python
  trap "echo -e \"\n[SSH] Caught Ctrl+C! Forwarding gracefully to Python (PID: $PYTHON_PID)...\"; kill -INT $PYTHON_PID" INT
  
  # 3. Stream the logs live
  tail --pid=$PYTHON_PID -n 0 -f ~/NemoHeadUnit-Wireless/deploy_remote_micromamba.log &
  
  # 4. Wait for Python to finish
  while kill -0 $PYTHON_PID 2>/dev/null; do
      wait $PYTHON_PID
  done
'