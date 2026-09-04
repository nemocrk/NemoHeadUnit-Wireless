#!/usr/bin/env bash
# test_hwaccel_diag.sh — Hardware Acceleration & Video Decoder Diagnostic Tool for NemoHeadUnit
set -e

echo "============================================================"
echo " 🎬 NemoHeadUnit Hardware Acceleration & Decoder Diagnostics"
echo " Date: $(date)"
echo " Host: $(hostname 2>/dev/null || uname -n) ($(uname -m) - $(uname -s))"
echo "============================================================"
echo ""

echo "--- 1. GPU Render Nodes (/dev/dri) & Hardware Probes ---"
if [ -d /dev/dri ]; then
  ls -la /dev/dri
else
  echo "ℹ️ Note: /dev/dri directory does not exist (may be WSL, Windows, or headless container without direct DRI access)."
fi

if command -v nvidia-smi &>/dev/null; then
  echo "🟢 NVIDIA GPU detected:"
  nvidia-smi --query-gpu=name,driver_version,utilization.gpu,memory.total --format=csv,noheader 2>/dev/null || nvidia-smi
fi
echo ""

echo "--- 2. User & Group Access ---"
echo "Current user: $(whoami)"
echo "Groups: $(groups 2>/dev/null || id -Gn)"
if groups 2>/dev/null | grep -E -q "render|video"; then
  echo "🟢 User belongs to render/video group."
else
  echo "ℹ️ Note: User does not belong to 'render' or 'video' group (if running on native Linux with DRM, add with: sudo usermod -a -G video,render $(whoami))"
fi
echo ""

echo "--- 3. VA-API Driver Status (vainfo) ---"
if command -v vainfo &>/dev/null; then
  vainfo 2>&1 || echo "⚠️ vainfo failed to initialize VA-API display"
else
  echo "ℹ️ vainfo not installed."
  if command -v pacman &>/dev/null; then
    echo "   Arch Linux: sudo pacman -S libva-utils (and intel-media-driver / libva-mesa-driver)"
  elif command -v apt-get &>/dev/null; then
    echo "   Debian/Ubuntu: sudo apt install vainfo (and intel-media-va-driver / mesa-va-drivers)"
  fi
fi
echo ""

echo "--- 4. FFmpeg Hardware Accelerators ---"
if command -v ffmpeg &>/dev/null; then
  ffmpeg -hide_banner -hwaccels
else
  echo "ℹ️ ffmpeg is not installed"
fi
echo ""

echo "--- 5. GStreamer Multi-Vendor Hardware Decoders ---"
if command -v gst-inspect-1.0 &>/dev/null; then
  DECODERS=("nvh264dec" "nvh264sldec" "vah264dec" "vaapih264dec" "d3d11h264dec" "qsvh264dec" "v4l2slh264dec" "v4l2h264dec" "avdec_h264" "qml6glsink" "jpegenc")
  for elem in "${DECODERS[@]}"; do
    if gst-inspect-1.0 "$elem" &>/dev/null; then
      echo "  ✅ $elem: Available"
    else
      echo "  ⚪ $elem: Not found"
    fi
  done
else
  echo "ℹ️ gst-inspect-1.0 is not in PATH."
fi
echo ""

echo "--- 6. Python Video Decoder Detection ---"
if command -v micromamba &>/dev/null; then
  PYTHON_CMD="micromamba run -n NemoHeadUnit-Wireless python"
elif command -v python3 &>/dev/null; then
  PYTHON_CMD="python3"
else
  PYTHON_CMD="python"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

$PYTHON_CMD -c "
import sys
from pathlib import Path
sys.path.insert(0, '${REPO_DIR}/backend')
try:
    from shared.hardware.video_decoder import get_available_decoders, get_best_hardware_decoder
    print('Available Decoders in GStreamer Registry:')
    for dec in get_available_decoders():
        status = '✅ Available' if dec['available'] else '⚪ Missing'
        hw = '[HW]' if dec['is_hardware'] else '[SW]'
        print(f'  {status} {hw:4s} {dec[\"element\"]:16s} - {dec[\"description\"]}')
    best, desc = get_best_hardware_decoder()
    print(f'\nOptimal Decoder Selected: {best}')
    print(f'Description: {desc}')
except Exception as e:
    print(f'Error running Python video decoder probe: {e}')
" 2>/dev/null || true

echo ""
echo "============================================================"
