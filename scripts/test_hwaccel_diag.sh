#!/usr/bin/env bash
# test_hwaccel_diag.sh — Hardware Acceleration & VA-API Diagnostic Tool for NemoHeadUnit
set -e

echo "============================================================"
echo " 🎬 NemoHeadUnit Hardware Acceleration & VA-API Diagnostics"
echo " Date: $(date)"
echo " Host: $(hostname) ($(uname -m))"
echo "============================================================"
echo ""

echo "--- 1. GPU Render Nodes (/dev/dri) ---"
if [ -d /dev/dri ]; then
  ls -la /dev/dri
else
  echo "⚠️ WARNING: /dev/dri directory does NOT exist! (No GPU drivers loaded)"
fi
echo ""

echo "--- 2. User & Group Access ---"
echo "Current user: $(whoami)"
echo "Groups: $(groups)"
if groups | grep -E -q "render|video"; then
  echo "🟢 User belongs to render/video group."
else
  echo "⚠️ WARNING: User does NOT belong to 'render' or 'video' group! Accessing /dev/dri/renderD128 may fail."
fi
echo ""

echo "--- 3. VA-API Driver Status (vainfo) ---"
if command -v vainfo &>/dev/null; then
  vainfo 2>&1 || echo "⚠️ vainfo failed to initialize VA-API display"
else
  echo "⚠️ vainfo is NOT installed. Run: sudo apt install vainfo i965-va-driver"
fi
echo ""

echo "--- 4. FFmpeg Hardware Accelerators ---"
if command -v ffmpeg &>/dev/null; then
  ffmpeg -hide_banner -hwaccels
else
  echo "⚠️ ffmpeg is NOT installed"
fi
echo ""

echo "--- 5. GStreamer VA-API & JPEG Plugins ---"
if command -v gst-inspect-1.0 &>/dev/null; then
  echo "Checking GStreamer vaapih264dec:"
  gst-inspect-1.0 vaapih264dec 2>&1 | head -n 12 || echo "  (vaapih264dec missing)"
  echo "Checking GStreamer jpegenc:"
  gst-inspect-1.0 jpegenc 2>&1 | head -n 8 || echo "  (jpegenc missing)"
else
  echo "⚠️ gst-inspect-1.0 is NOT installed. Run: sudo apt install gstreamer1.0-tools"
fi
echo ""

echo "============================================================"
