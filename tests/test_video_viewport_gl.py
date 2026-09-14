"""Tests for VideoViewportWidget GL path integration."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import sys
import types
from PyQt6.QtWidgets import QApplication

# Ensure QApplication exists once for test process
_app = QApplication.instance() or QApplication(sys.argv)

from backend.modules.qt6_gui.ui import video_viewport as mod


def _make_mock_decoder(is_available=True, texture_id=42):
    return types.SimpleNamespace(
        is_available=is_available,
        set_gl_context=lambda *a: None,
        decode_nal=lambda *a: True,
        get_latest_texture_id=lambda: texture_id,
        close=lambda: None,
    )


def test_attach_gl_decoder_method_exists():
    """VideoViewportWidget must expose attach_gl_decoder()."""
    assert hasattr(mod.VideoViewportWidget, "attach_gl_decoder")


def test_attach_gl_decoder_stores_decoder():
    """attach_gl_decoder stores the decoder on the instance."""
    w = mod.VideoViewportWidget()
    decoder = _make_mock_decoder()
    w.attach_gl_decoder(decoder)
    assert w._gl_decoder is decoder


def test_update_frame_no_op_when_gl_active():
    """update_frame() must not store bytes when GL decoder is active."""
    w = mod.VideoViewportWidget()
    w._gl_decoder = _make_mock_decoder(is_available=True)
    w.current_frame_data = None
    w.update_frame(b"fake_rgba", 1280, 720)
    assert w.current_frame_data is None


def test_update_frame_stores_bytes_when_gl_unavailable():
    """update_frame() stores rgba_bytes when GL decoder is None."""
    w = mod.VideoViewportWidget()
    w._gl_decoder = None
    w.current_frame_data = None
    w.update_frame(b"x" * (1280 * 720 * 4), 1280, 720)
    assert w.current_frame_data is not None


def test_viewport_has_gl_decoder_attr_after_init():
    """VideoViewportWidget instances must have _gl_decoder=None after __init__."""
    w = mod.VideoViewportWidget()
    assert hasattr(w, "_gl_decoder")
    assert w._gl_decoder is None
