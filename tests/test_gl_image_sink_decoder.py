"""Tests for Qml6ZeroCopyDecoder and fallback in shm_media_engine."""
import sys
import importlib
import types


def _reload(mod_name):
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_has_qopengl_flag_is_bool():
    mod = _reload("backend.modules.qt6_gui.media.shm_media_engine")
    assert isinstance(mod._HAS_QOPENGL, bool)


def test_gl_image_sink_decoder_class_exists():
    mod = _reload("backend.modules.qt6_gui.media.shm_media_engine")
    assert hasattr(mod, "Qml6ZeroCopyDecoder")


def test_gl_image_sink_decoder_interface():
    """Qml6ZeroCopyDecoder must expose the full required interface regardless of GL/HW availability."""
    mod = _reload("backend.modules.qt6_gui.media.shm_media_engine")
    dec = mod.Qml6ZeroCopyDecoder(on_frame_callback=lambda *a: None)
    assert hasattr(dec, "is_available")
    assert hasattr(dec, "decode_nal")
    assert hasattr(dec, "close")
    assert isinstance(dec.is_available, bool)


def test_gl_image_sink_decoder_decode_nal_returns_false_before_context():
    """decode_nal must return False before sink is bound."""
    mod = _reload("backend.modules.qt6_gui.media.shm_media_engine")
    dec = mod.Qml6ZeroCopyDecoder(on_frame_callback=lambda *a: None)
    dec.is_available = True
    dec._is_sink_bound = False
    result = dec.decode_nal(b"\x00\x00\x00\x01\x65", 0)
    assert result is False


def test_shm_engine_uses_gl_decoder_when_available(monkeypatch):
    """QtSHMMediaEngine picks Qml6ZeroCopyDecoder when available."""
    mod = _reload("backend.modules.qt6_gui.media.shm_media_engine")
    fake_dec = types.SimpleNamespace(
        is_available=True,
        decode_nal=lambda *a: True,
        close=lambda: None,
    )
    monkeypatch.setattr(mod, "Qml6ZeroCopyDecoder", lambda **kw: fake_dec)
    engine = mod.QtSHMMediaEngine.__new__(mod.QtSHMMediaEngine)
    mod.QtSHMMediaEngine.__init__(engine)
    assert engine._hw_decoder is fake_dec


def test_shm_engine_uses_hw_decoder_when_gl_unavailable(monkeypatch):
    """QtSHMMediaEngine uses GStreamerHwDecoder when Qml6ZeroCopyDecoder is unavailable."""
    mod = _reload("backend.modules.qt6_gui.media.shm_media_engine")
    fake_dec = types.SimpleNamespace(
        is_available=False,
        close=lambda: None,
    )
    monkeypatch.setattr(mod, "Qml6ZeroCopyDecoder", lambda **kw: fake_dec)
    engine = mod.QtSHMMediaEngine.__new__(mod.QtSHMMediaEngine)
    mod.QtSHMMediaEngine.__init__(engine)
    assert isinstance(engine._hw_decoder, mod.GStreamerHwDecoder)
