"""Tests for GlImageSinkDecoder and _HAS_QOPENGL flag in shm_media_engine."""
import sys
import importlib


def _reload(mod_name):
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_has_qopengl_flag_is_bool():
    mod = _reload("backend.modules.qt6_gui.media.shm_media_engine")
    assert isinstance(mod._HAS_QOPENGL, bool)


def test_gl_image_sink_decoder_class_exists():
    mod = _reload("backend.modules.qt6_gui.media.shm_media_engine")
    assert hasattr(mod, "GlImageSinkDecoder")


def test_gl_image_sink_decoder_interface():
    """GlImageSinkDecoder must expose the full required interface regardless of GL/HW availability."""
    mod = _reload("backend.modules.qt6_gui.media.shm_media_engine")
    dec = mod.GlImageSinkDecoder(on_frame_callback=lambda *a: None)
    assert hasattr(dec, "is_available")
    assert hasattr(dec, "set_gl_context")
    assert hasattr(dec, "decode_nal")
    assert hasattr(dec, "get_latest_texture_id")
    assert hasattr(dec, "close")
    assert isinstance(dec.is_available, bool)
    assert dec.get_latest_texture_id() == 0


def test_gl_image_sink_decoder_decode_nal_returns_false_before_context():
    """decode_nal must return False before set_gl_context is called."""
    mod = _reload("backend.modules.qt6_gui.media.shm_media_engine")
    dec = mod.GlImageSinkDecoder(on_frame_callback=lambda *a: None)
    dec.is_available = True
    dec._gl_context_set = False
    result = dec.decode_nal(b"\x00\x00\x00\x01\x65", 0)
    assert result is False


def test_shm_engine_uses_gl_decoder_when_available(monkeypatch):
    """QtSHMMediaEngine picks GlImageSinkDecoder when _HAS_QOPENGL is True."""
    mod = _reload("backend.modules.qt6_gui.media.shm_media_engine")
    monkeypatch.setattr(mod, "_HAS_QOPENGL", True)
    import types
    fake_dec = types.SimpleNamespace(
        is_available=True,
        set_gl_context=lambda *a: None,
        decode_nal=lambda *a: True,
        get_latest_texture_id=lambda: 0,
        close=lambda: None,
    )
    monkeypatch.setattr(mod, "GlImageSinkDecoder", lambda **kw: fake_dec)
    engine = mod.QtSHMMediaEngine.__new__(mod.QtSHMMediaEngine)
    mod.QtSHMMediaEngine.__init__(engine)
    assert engine._hw_decoder is fake_dec


def test_shm_engine_uses_hw_decoder_when_gl_unavailable(monkeypatch):
    """QtSHMMediaEngine uses GStreamerHwDecoder when _HAS_QOPENGL is False."""
    mod = _reload("backend.modules.qt6_gui.media.shm_media_engine")
    monkeypatch.setattr(mod, "_HAS_QOPENGL", False)
    engine = mod.QtSHMMediaEngine.__new__(mod.QtSHMMediaEngine)
    mod.QtSHMMediaEngine.__init__(engine)
    assert isinstance(engine._hw_decoder, mod.GStreamerHwDecoder)
