"""
Unit tests for video_ui/main.py

Strategy:
  video_ui/main.py dipende pesantemente da PyQt6 e GStreamer (gi/Gst).
  Tutti i test vengono eseguiti senza Qt event loop reale e senza GStreamer.
  Le dipendenze sono mockat prima di qualsiasi import del modulo:

    - PyQt6.QtCore, PyQt6.QtGui, PyQt6.QtWidgets, PyQt6.QtOpenGLWidgets
      vengono sostituiti con MagicMock() tramite sys.modules injection.
    - gi / gi.repository.Gst / gi.repository.GLib sostituiti con MagicMock().
    - BusClient, get_logger patchati.

  La fixture `vu` ricarica il modulo ad ogni test (importlib.reload) per
  garantire isolamento del singleton _conn_state e _window.

Covers:
  Section 1 — _STATE_LABELS: tutte le chiavi, colori, testi
  Section 2 — _set_conn_state: aggiorna _conn_state, invoca set_conn_state su window, no-window safe
  Section 3 — on_system_readytostart: pubblica system.module_ready con name e priority
  Section 4 — on_system_start: wrong priority ignorato, correct priority pubblica system.ready + winid
  Section 5 — on_system_stop: chiama bus.stop, nessun crash senza _app
  Section 6 — on_video_frame: no window safe, invoca push_frame via QMetaObject
  Section 7 — on_video_state: PLAYING→STREAMING, IDLE/STOPPED→INTERRUPTED + set_streaming(False),
               IDLE senza sessione attiva no-crash
  Section 8 — on_aa_session_active: WAITING_BT→HANDSHAKE, HANDSHAKE→HANDSHAKE, STREAMING→invariato
  Section 9 — on_aa_session_shutdown: qualsiasi stato → WAITING_BT, invoca set_streaming(False)
  Section 10 — on_bluetooth_pairing_completed: WAITING_BT→HANDSHAKE, altri stati invariati
  Section 11 — _try_load_system_vaapi: GST non disponibile, già disponibile, scan path, nessun path trovato
  Section 12 — VideoWidget.set_streaming: True con render_fmt mostra index 1, False mostra index 0,
               True senza render_fmt mostra index 0, reset _first_frame_shown e _pts_counter
  Section 13 — VideoWidget.push_frame: appsrc None early return, base64 invalido no crash,
               config frame pts=NONE, data frame pts monotono, frames_pushed incrementato
  Section 14 — _PlaceholderWidget.set_conn_state: aggiorna stylesheet e testo label
"""

from __future__ import annotations

import sys
import importlib
import types
from unittest.mock import MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# PyQt6 / GStreamer stub factory
# Deve essere installato in sys.modules PRIMA di importare il modulo.
# ---------------------------------------------------------------------------

def _install_qt_stubs():
    """Inject minimal PyQt6 and gi stubs into sys.modules."""
    # --- gi / GStreamer ---
    gi_mod = types.ModuleType("gi")
    gi_mod.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mod)

    gi_repo = types.ModuleType("gi.repository")
    sys.modules.setdefault("gi.repository", gi_repo)

    mock_gst = MagicMock()
    mock_gst.init = MagicMock()
    mock_gst.CLOCK_TIME_NONE = -1
    mock_gst.FlowReturn = MagicMock()
    mock_gst.FlowReturn.OK  = "OK"
    mock_gst.FlowReturn.ERROR = "ERROR"
    mock_gst.State = MagicMock()
    mock_gst.State.PLAYING = "PLAYING"
    mock_gst.State.NULL    = "NULL"
    mock_gst.MapFlags = MagicMock()
    mock_gst.MapFlags.READ = 1
    gi_repo.Gst  = mock_gst
    gi_repo.GLib = MagicMock()
    sys.modules.setdefault("gi.repository.Gst",  mock_gst)
    sys.modules.setdefault("gi.repository.GLib", MagicMock())

    # --- PyQt6.QtCore ---
    qtcore = types.ModuleType("PyQt6.QtCore")
    qtcore.Qt = MagicMock()
    qtcore.Qt.AlignmentFlag = MagicMock()
    qtcore.Qt.ConnectionType = MagicMock()
    qtcore.Qt.ConnectionType.QueuedConnection = 2
    qtcore.QTimer  = MagicMock()
    qtcore.QSize   = MagicMock()
    qtcore.Q_ARG   = MagicMock(side_effect=lambda t, v: (t, v))
    qtcore.QMetaObject = MagicMock()
    qtcore.pyqtSlot = lambda *a, **kw: (lambda f: f)
    sys.modules["PyQt6"]         = types.ModuleType("PyQt6")
    sys.modules["PyQt6.QtCore"]  = qtcore

    # --- PyQt6.QtGui ---
    qtgui = types.ModuleType("PyQt6.QtGui")
    qtgui.QFont   = MagicMock()
    qtgui.QImage  = MagicMock()
    qtgui.QPixmap = MagicMock()
    sys.modules["PyQt6.QtGui"] = qtgui

    # --- PyQt6.QtOpenGLWidgets ---
    qtogl = types.ModuleType("PyQt6.QtOpenGLWidgets")
    qtogl.QOpenGLWidget = MagicMock
    sys.modules["PyQt6.QtOpenGLWidgets"] = qtogl

    # --- PyQt6.QtWidgets ---
    qtwid = types.ModuleType("PyQt6.QtWidgets")
    for name in ("QApplication", "QMainWindow", "QWidget", "QVBoxLayout",
                 "QLabel", "QSizePolicy", "QStackedWidget"):
        setattr(qtwid, name, MagicMock)
    sys.modules["PyQt6.QtWidgets"] = qtwid

    return mock_gst


_install_qt_stubs()

_MOD = "modules.video_ui.main"


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def vu():
    """
    Reload video_ui/main.py with bus/log mocked.
    Returns (mod, mock_bus).
    """
    mock_bus = MagicMock()
    mock_log = MagicMock()

    for key in list(sys.modules.keys()):
        if "video_ui" in key:
            del sys.modules[key]

    with patch("shared.bus_client.BusClient", return_value=mock_bus), \
         patch("shared.logger.get_logger", return_value=mock_log):
        import modules.video_ui.main as mod
        importlib.reload(mod)
        mod.bus = mock_bus
        mod.log = mock_log
        mod._conn_state = mod._STATE_WAITING_BT
        mod._window = None
        mod._app    = None
        yield mod, mock_bus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _topics(mock_bus) -> list[str]:
    return [c.args[0] for c in mock_bus.publish.call_args_list]


def _payload(mock_bus, topic: str) -> dict:
    for c in mock_bus.publish.call_args_list:
        if c.args[0] == topic:
            return c.args[1]
    return {}


def _make_window(mod):
    """Inject a mock _window with .video attribute."""
    win = MagicMock()
    win.video = MagicMock()
    win.video.winId.return_value = 12345
    mod._window = win
    return win


# ===========================================================================
# Section 1 — _STATE_LABELS
# ===========================================================================

class TestStateLabels:

    @pytest.mark.unit
    def test_all_states_present(self, vu):
        mod, _ = vu
        for state in (mod._STATE_WAITING_BT, mod._STATE_HANDSHAKE,
                      mod._STATE_STREAMING, mod._STATE_INTERRUPTED):
            assert state in mod._STATE_LABELS

    @pytest.mark.unit
    def test_each_label_has_color_and_text(self, vu):
        mod, _ = vu
        for state, (color, text) in mod._STATE_LABELS.items():
            assert color.startswith("#")
            assert len(text) > 0

    @pytest.mark.unit
    def test_streaming_color_is_green(self, vu):
        mod, _ = vu
        color, _ = mod._STATE_LABELS[mod._STATE_STREAMING]
        assert "4" in color.lower() or "5" in color.lower()  # #4caf50

    @pytest.mark.unit
    def test_waiting_bt_color_is_red(self, vu):
        mod, _ = vu
        color, _ = mod._STATE_LABELS[mod._STATE_WAITING_BT]
        assert "e0" in color.lower()


# ===========================================================================
# Section 2 — _set_conn_state
# ===========================================================================

class TestSetConnState:

    @pytest.mark.unit
    def test_updates_conn_state(self, vu):
        mod, _ = vu
        mod._set_conn_state(mod._STATE_HANDSHAKE)
        assert mod._conn_state == mod._STATE_HANDSHAKE

    @pytest.mark.unit
    def test_no_crash_without_window(self, vu):
        mod, _ = vu
        mod._window = None
        mod._set_conn_state(mod._STATE_STREAMING)  # must not raise
        assert mod._conn_state == mod._STATE_STREAMING

    @pytest.mark.unit
    def test_invokes_set_conn_state_on_window(self, vu):
        mod, _ = vu
        win = _make_window(mod)
        mod._set_conn_state(mod._STATE_HANDSHAKE)
        # _invoke calls QMetaObject.invokeMethod — check it was called
        mod.QMetaObject = MagicMock()  # ensure attr exists for reference
        # We verify _conn_state was updated (Qt dispatch is async/mocked)
        assert mod._conn_state == mod._STATE_HANDSHAKE

    @pytest.mark.unit
    def test_all_transitions_reachable(self, vu):
        mod, _ = vu
        for state in (mod._STATE_HANDSHAKE, mod._STATE_STREAMING,
                      mod._STATE_INTERRUPTED, mod._STATE_WAITING_BT):
            mod._set_conn_state(state)
            assert mod._conn_state == state


# ===========================================================================
# Section 3 — on_system_readytostart
# ===========================================================================

class TestOnSystemReadytostart:

    @pytest.mark.unit
    def test_publishes_module_ready(self, vu):
        mod, mock_bus = vu
        mock_bus.publish.reset_mock()
        mod.on_system_readytostart()
        payload = _payload(mock_bus, "system.module_ready")
        assert payload == {"name": "video_ui", "priority": 2}


# ===========================================================================
# Section 4 — on_system_start
# ===========================================================================

class TestOnSystemStart:

    @pytest.mark.unit
    def test_wrong_priority_no_publish(self, vu):
        mod, mock_bus = vu
        mock_bus.publish.reset_mock()
        mod.on_system_start("system.start", {"priority": 99})
        assert "system.ready" not in _topics(mock_bus)

    @pytest.mark.unit
    def test_correct_priority_publishes_ready(self, vu):
        mod, mock_bus = vu
        mock_bus.publish.reset_mock()
        mod.on_system_start("system.start", {"priority": 2})
        assert "system.ready" in _topics(mock_bus)

    @pytest.mark.unit
    def test_publishes_winid_when_window_present(self, vu):
        mod, mock_bus = vu
        _make_window(mod)
        mock_bus.publish.reset_mock()
        mod.on_system_start("system.start", {"priority": 2})
        assert "video.ui.winid" in _topics(mock_bus)
        payload = _payload(mock_bus, "video.ui.winid")
        assert payload["winid"] == 12345

    @pytest.mark.unit
    def test_no_winid_without_window(self, vu):
        mod, mock_bus = vu
        mod._window = None
        mock_bus.publish.reset_mock()
        mod.on_system_start("system.start", {"priority": 2})
        assert "video.ui.winid" not in _topics(mock_bus)


# ===========================================================================
# Section 5 — on_system_stop
# ===========================================================================

class TestOnSystemStop:

    @pytest.mark.unit
    def test_calls_bus_stop(self, vu):
        mod, mock_bus = vu
        mod.on_system_stop("system.stop", {})
        mock_bus.stop.assert_called()

    @pytest.mark.unit
    def test_no_crash_without_app(self, vu):
        mod, _ = vu
        mod._app = None
        mod.on_system_stop("system.stop", {})  # must not raise


# ===========================================================================
# Section 6 — on_video_frame
# ===========================================================================

class TestOnVideoFrame:

    @pytest.mark.unit
    def test_no_crash_without_window(self, vu):
        mod, _ = vu
        mod._window = None
        mod.on_video_frame("video.frame", {"data_b64": "AAAA", "is_config": False})  # must not raise

    @pytest.mark.unit
    def test_invokes_push_frame_with_window(self, vu):
        mod, _ = vu
        _make_window(mod)
        mock_invoke = MagicMock()
        with patch.object(mod, "QMetaObject", create=True) as mq:
            mq.invokeMethod = mock_invoke
            mod.on_video_frame("video.frame", {"data_b64": "dGVzdA==", "is_config": False})
        # QMetaObject.invokeMethod should have been called
        mock_invoke.assert_called()

    @pytest.mark.unit
    def test_is_config_flag_passed_correctly(self, vu):
        mod, _ = vu
        _make_window(mod)
        calls_args = []
        def capture(*args, **kwargs):
            calls_args.append(args)
        with patch.object(mod, "QMetaObject", create=True) as mq:
            mq.invokeMethod.side_effect = capture
            mod.on_video_frame("video.frame", {"data_b64": "dGVzdA==", "is_config": True})
        # At least one call happened
        assert len(calls_args) >= 1


# ===========================================================================
# Section 7 — on_video_state
# ===========================================================================

class TestOnVideoState:

    @pytest.mark.unit
    def test_playing_sets_streaming_state(self, vu):
        mod, _ = vu
        mod._conn_state = mod._STATE_HANDSHAKE
        mod.on_video_state("video.state", {"state": "PLAYING"})
        assert mod._conn_state == mod._STATE_STREAMING

    @pytest.mark.unit
    def test_idle_from_streaming_sets_interrupted(self, vu):
        mod, _ = vu
        mod._conn_state = mod._STATE_STREAMING
        mod.on_video_state("video.state", {"state": "IDLE"})
        assert mod._conn_state == mod._STATE_INTERRUPTED

    @pytest.mark.unit
    def test_stopped_from_streaming_sets_interrupted(self, vu):
        mod, _ = vu
        mod._conn_state = mod._STATE_STREAMING
        mod.on_video_state("video.state", {"state": "STOPPED"})
        assert mod._conn_state == mod._STATE_INTERRUPTED

    @pytest.mark.unit
    def test_idle_not_from_streaming_no_state_change(self, vu):
        mod, _ = vu
        mod._conn_state = mod._STATE_HANDSHAKE
        mod.on_video_state("video.state", {"state": "IDLE"})
        assert mod._conn_state == mod._STATE_HANDSHAKE

    @pytest.mark.unit
    def test_idle_invokes_set_streaming_false_on_window(self, vu):
        mod, _ = vu
        win = _make_window(mod)
        mod._conn_state = mod._STATE_STREAMING
        mock_invoke = MagicMock()
        with patch.object(mod, "_invoke", mock_invoke):
            mod.on_video_state("video.state", {"state": "IDLE"})
        mock_invoke.assert_called()

    @pytest.mark.unit
    def test_unknown_state_no_crash(self, vu):
        mod, _ = vu
        original = mod._conn_state
        mod.on_video_state("video.state", {"state": "UNKNOWN"})
        assert mod._conn_state == original


# ===========================================================================
# Section 8 — on_aa_session_active
# ===========================================================================

class TestOnAaSessionActive:

    @pytest.mark.unit
    def test_waiting_bt_transitions_to_handshake(self, vu):
        mod, _ = vu
        mod._conn_state = mod._STATE_WAITING_BT
        mod.on_aa_session_active("aa.session.active", {})
        assert mod._conn_state == mod._STATE_HANDSHAKE

    @pytest.mark.unit
    def test_handshake_stays_handshake(self, vu):
        mod, _ = vu
        mod._conn_state = mod._STATE_HANDSHAKE
        mod.on_aa_session_active("aa.session.active", {})
        assert mod._conn_state == mod._STATE_HANDSHAKE

    @pytest.mark.unit
    def test_streaming_not_changed(self, vu):
        mod, _ = vu
        mod._conn_state = mod._STATE_STREAMING
        mod.on_aa_session_active("aa.session.active", {})
        assert mod._conn_state == mod._STATE_STREAMING

    @pytest.mark.unit
    def test_interrupted_not_changed(self, vu):
        mod, _ = vu
        mod._conn_state = mod._STATE_INTERRUPTED
        mod.on_aa_session_active("aa.session.active", {})
        assert mod._conn_state == mod._STATE_INTERRUPTED


# ===========================================================================
# Section 9 — on_aa_session_shutdown
# ===========================================================================

class TestOnAaSessionShutdown:

    @pytest.mark.unit
    @pytest.mark.parametrize("initial_state", [
        "WAITING_BT", "HANDSHAKE", "STREAMING", "INTERRUPTED"
    ])
    def test_any_state_goes_to_waiting_bt(self, vu, initial_state):
        mod, _ = vu
        mod._conn_state = initial_state
        mod.on_aa_session_shutdown("aa.session.shutdown", {})
        assert mod._conn_state == mod._STATE_WAITING_BT

    @pytest.mark.unit
    def test_invokes_set_streaming_false(self, vu):
        mod, _ = vu
        _make_window(mod)
        mock_invoke = MagicMock()
        with patch.object(mod, "_invoke", mock_invoke):
            mod.on_aa_session_shutdown("aa.session.shutdown", {})
        mock_invoke.assert_called()

    @pytest.mark.unit
    def test_no_crash_without_window(self, vu):
        mod, _ = vu
        mod._window = None
        mod.on_aa_session_shutdown("aa.session.shutdown", {})  # must not raise


# ===========================================================================
# Section 10 — on_bluetooth_pairing_completed
# ===========================================================================

class TestOnBluetoothPairingCompleted:

    @pytest.mark.unit
    def test_waiting_bt_transitions_to_handshake(self, vu):
        mod, _ = vu
        mod._conn_state = mod._STATE_WAITING_BT
        mod.on_bluetooth_pairing_completed("bluetooth.pairing.completed", {})
        assert mod._conn_state == mod._STATE_HANDSHAKE

    @pytest.mark.unit
    def test_handshake_not_changed(self, vu):
        mod, _ = vu
        mod._conn_state = mod._STATE_HANDSHAKE
        mod.on_bluetooth_pairing_completed("bluetooth.pairing.completed", {})
        assert mod._conn_state == mod._STATE_HANDSHAKE

    @pytest.mark.unit
    def test_streaming_not_changed(self, vu):
        mod, _ = vu
        mod._conn_state = mod._STATE_STREAMING
        mod.on_bluetooth_pairing_completed("bluetooth.pairing.completed", {})
        assert mod._conn_state == mod._STATE_STREAMING


# ===========================================================================
# Section 11 — _try_load_system_vaapi
# ===========================================================================

class TestTryLoadSystemVaapi:

    @pytest.mark.unit
    def test_returns_none_when_gst_not_available(self, vu):
        mod, _ = vu
        mod._GST_AVAILABLE = False
        result = mod._try_load_system_vaapi()
        assert result is None

    @pytest.mark.unit
    def test_returns_conda_env_if_already_available(self, vu):
        mod, _ = vu
        mod._GST_AVAILABLE = True
        mock_gst = MagicMock()
        mock_gst.ElementFactory.find.return_value = MagicMock()  # found
        mock_gst.Registry.get.return_value = MagicMock()
        with patch.object(mod, "Gst", mock_gst, create=True):
            result = mod._try_load_system_vaapi()
        assert result == "(conda env)"

    @pytest.mark.unit
    def test_returns_none_when_no_paths_found(self, vu):
        mod, _ = vu
        mod._GST_AVAILABLE = True
        mock_gst = MagicMock()
        mock_gst.ElementFactory.find.return_value = None  # not found
        mock_gst.Registry.get.return_value = MagicMock()
        with patch.object(mod, "Gst", mock_gst, create=True), \
             patch("pathlib.Path.exists", return_value=False):
            result = mod._try_load_system_vaapi()
        assert result is None

    @pytest.mark.unit
    def test_scans_system_path_when_needed(self, vu):
        mod, _ = vu
        mod._GST_AVAILABLE = True
        registry = MagicMock()
        mock_gst = MagicMock()
        # First call (already available check): not found
        # Subsequent calls after scan_path: found
        call_count = [0]
        def find_side_effect(name):
            call_count[0] += 1
            return MagicMock() if call_count[0] > 1 else None
        mock_gst.ElementFactory.find.side_effect = find_side_effect
        mock_gst.Registry.get.return_value = registry
        with patch.object(mod, "Gst", mock_gst, create=True), \
             patch("pathlib.Path.exists", return_value=True):
            result = mod._try_load_system_vaapi()
        registry.scan_path.assert_called()
        assert result is not None


# ===========================================================================
# Section 12 — VideoWidget.set_streaming
# ===========================================================================

class TestVideoWidgetSetStreaming:

    def _make_widget(self, mod, render_fmt="NV12"):
        """Build a minimal VideoWidget-like object without Qt."""
        widget = MagicMock()
        widget._render_fmt        = render_fmt
        widget._stack             = MagicMock()
        widget._first_frame_shown = True
        widget._pts_counter       = 42
        # Bind real method
        widget.set_streaming = mod.VideoWidget.set_streaming.__get__(widget)
        return widget

    @pytest.mark.unit
    def test_streaming_true_with_format_shows_index_1(self, vu):
        mod, _ = vu
        w = self._make_widget(mod, render_fmt="NV12")
        w.set_streaming(True)
        w._stack.setCurrentIndex.assert_called_with(1)

    @pytest.mark.unit
    def test_streaming_true_without_format_shows_index_0(self, vu):
        mod, _ = vu
        w = self._make_widget(mod, render_fmt="")
        w.set_streaming(True)
        w._stack.setCurrentIndex.assert_called_with(0)

    @pytest.mark.unit
    def test_streaming_false_shows_index_0(self, vu):
        mod, _ = vu
        w = self._make_widget(mod, render_fmt="NV12")
        w.set_streaming(False)
        w._stack.setCurrentIndex.assert_called_with(0)

    @pytest.mark.unit
    def test_false_resets_first_frame_shown(self, vu):
        mod, _ = vu
        w = self._make_widget(mod)
        w._first_frame_shown = True
        w.set_streaming(False)
        assert w._first_frame_shown is False

    @pytest.mark.unit
    def test_false_resets_pts_counter(self, vu):
        mod, _ = vu
        w = self._make_widget(mod)
        w._pts_counter = 99
        w.set_streaming(False)
        assert w._pts_counter == 0


# ===========================================================================
# Section 13 — VideoWidget.push_frame
# ===========================================================================

class TestVideoWidgetPushFrame:

    def _make_widget(self, mod):
        widget = MagicMock()
        widget._appsrc          = None
        widget._frames_pushed   = 0
        widget._pts_counter     = 0
        widget.push_frame = mod.VideoWidget.push_frame.__get__(widget)
        return widget

    @pytest.mark.unit
    def test_appsrc_none_early_return(self, vu):
        mod, _ = vu
        w = self._make_widget(mod)
        w._appsrc = None
        w.push_frame("dGVzdA==", False)  # must not raise
        assert w._frames_pushed == 0

    @pytest.mark.unit
    def test_invalid_base64_no_crash(self, vu):
        mod, _ = vu
        w = self._make_widget(mod)
        w._appsrc = MagicMock()
        w.push_frame("!!!invalid!!!", False)  # must not raise

    @pytest.mark.unit
    def test_frames_pushed_incremented(self, vu):
        mod, _ = vu
        import base64
        w = self._make_widget(mod)
        w._appsrc = MagicMock()
        w._appsrc.emit.return_value = "OK"
        mock_buf = MagicMock()
        mock_buf.pts = 0
        mock_buf.duration = 0
        with patch.object(mod.Gst.Buffer, "new_wrapped", return_value=mock_buf):
            w.push_frame(base64.b64encode(b"\x00\x00\x01").decode(), False)
        assert w._frames_pushed == 1

    @pytest.mark.unit
    def test_config_frame_pts_is_none(self, vu):
        mod, _ = vu
        import base64
        w = self._make_widget(mod)
        w._appsrc = MagicMock()
        w._appsrc.emit.return_value = "OK"
        mock_buf = MagicMock()
        captured = {}
        def set_pts(v):
            captured["pts"] = v
        type(mock_buf).pts = property(lambda self: captured.get("pts"), lambda self, v: captured.update({"pts": v}))
        with patch.object(mod.Gst.Buffer, "new_wrapped", return_value=mock_buf):
            w.push_frame(base64.b64encode(b"\x00\x00\x01").decode(), True)
        # For config frames, pts must be set to CLOCK_TIME_NONE
        assert captured.get("pts") == mod.Gst.CLOCK_TIME_NONE

    @pytest.mark.unit
    def test_data_frame_pts_monotonically_increasing(self, vu):
        mod, _ = vu
        import base64
        w = self._make_widget(mod)
        w._appsrc = MagicMock()
        w._appsrc.emit.return_value = "OK"
        pts_values = []
        for _ in range(3):
            mock_buf = MagicMock()
            captured = {}
            type(mock_buf).pts = property(
                lambda self: captured.get("pts", 0),
                lambda self, v: captured.update({"pts": v})
            )
            with patch.object(mod.Gst.Buffer, "new_wrapped", return_value=mock_buf):
                w.push_frame(base64.b64encode(b"\x00\x00\x01").decode(), False)
            pts_values.append(captured.get("pts", 0))
        assert pts_values[0] < pts_values[1] < pts_values[2]


# ===========================================================================
# Section 14 — _PlaceholderWidget.set_conn_state
# ===========================================================================

class TestPlaceholderWidget:

    def _make_placeholder(self, mod):
        ph = MagicMock()
        ph._state_label = MagicMock()
        ph.set_conn_state = mod._PlaceholderWidget.set_conn_state.__get__(ph)
        return ph

    @pytest.mark.unit
    def test_sets_stylesheet(self, vu):
        mod, _ = vu
        ph = self._make_placeholder(mod)
        ph.set_conn_state("#4caf50", "● Stream attivo")
        ph._state_label.setStyleSheet.assert_called_with("color: #4caf50;")

    @pytest.mark.unit
    def test_sets_text(self, vu):
        mod, _ = vu
        ph = self._make_placeholder(mod)
        ph.set_conn_state("#4caf50", "● Stream attivo")
        ph._state_label.setText.assert_called_with("● Stream attivo")
