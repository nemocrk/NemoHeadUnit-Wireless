"""
test_main_window_and_viewport.py — Comprehensive Unit Tests for MainWindow & VideoViewportWidget
(backend.modules.qt6_gui.ui.main_window, backend.modules.qt6_gui.ui.video_viewport)
"""

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch
import pytest
from PyQt6.QtCore import Qt, QPointF, QEvent
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

os.environ["QT_QPA_PLATFORM"] = "offscreen"
from backend.modules.qt6_gui.ui.main_window import MainWindow
from backend.modules.qt6_gui.ui.video_viewport import VideoViewportWidget, FrameImageProvider

pytestmark = pytest.mark.unit


@pytest.fixture
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def main_win(app):
    win = MainWindow()
    # Prevent background network thread leaks in tests
    win.volume_popover.start_media_stream = MagicMock()
    win.bluetooth_drawer.start_stream = MagicMock()
    win.logs_drawer.start_stream = MagicMock()
    win.diagnostics_drawer.start_stream = MagicMock()
    win.settings_drawer.fetch_config = MagicMock()

    win.resize(1024, 600)
    win.show()
    app.processEvents()
    yield win
    try:
        win.close()
    except Exception:
        pass


def test_main_window_toggle_drawers(main_win):
    # Test toggling each drawer
    drawers = [
        main_win.phone_drawer,
        main_win.bluetooth_drawer,
        main_win.settings_drawer,
        main_win.logs_drawer,
        main_win.diagnostics_drawer,
    ]
    for drawer in drawers:
        assert drawer.isHidden()
        main_win._toggle_drawer(drawer)
        assert drawer.isVisible()
        # Toggle again to hide
        main_win._toggle_drawer(drawer)
        assert drawer.isHidden()


def test_main_window_clock_overlay_and_focus(main_win):
    modes = []
    main_win.focus_toggle_requested.connect(lambda mode: modes.append(mode))

    # Initial state is projected (True)
    assert main_win.isVideoFocused is True

    # Toggle to native/clock overlay
    main_win._toggle_clock_overlay()
    assert main_win.isVideoFocused is False
    assert modes[-1] == "NATIVE"
    assert main_win.disconnected_screen.isVisible()

    # Toggle back to projected
    main_win._toggle_clock_overlay()
    assert main_win.isVideoFocused is True
    assert modes[-1] == "PROJECTED"
    assert main_win.disconnected_screen.isHidden()


def test_main_window_volume_and_keys(main_win):
    # Toggle volume popover
    main_win._toggle_volume_popover()
    assert main_win.volume_popover.isVisible()
    main_win._toggle_volume_popover()
    assert main_win.volume_popover.isHidden()

    # Hardware volume actions
    with patch.object(main_win.volume_popover, "_on_vol_click") as m_click:
        main_win._on_hardware_volume_key("up")
        m_click.assert_called_with("up")
        assert main_win.volume_popover.isVisible()

        main_win._on_hardware_volume_key("down")
        m_click.assert_called_with("down")

        main_win._on_hardware_volume_key("mute")
        m_click.assert_called_with("mute")

    # Key press events for volume
    ev_up = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_VolumeUp, Qt.KeyboardModifier.NoModifier)
    assert main_win.eventFilter(main_win, ev_up) is True

    ev_down = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_VolumeDown, Qt.KeyboardModifier.NoModifier)
    assert main_win.eventFilter(main_win, ev_down) is True

    ev_mute = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_VolumeMute, Qt.KeyboardModifier.NoModifier)
    assert main_win.eventFilter(main_win, ev_mute) is True

    main_win.keyPressEvent(ev_up)
    main_win.keyPressEvent(ev_down)
    main_win.keyPressEvent(ev_mute)


def test_main_window_fullscreen_and_resize(main_win):
    main_win.set_fullscreen(True)
    assert main_win.isFullScreen()

    main_win._toggle_fullscreen()
    assert not main_win.isFullScreen()

    main_win.set_fullscreen(False)
    assert not main_win.isFullScreen()

    # Resize event
    main_win.resize(800, 480)
    assert main_win.width() == 800
    assert main_win.height() == 480


def test_main_window_dashboard_layouts(main_win):
    # 1. Idle / Default: only centered clock
    main_win.update_dashboard_state(has_nav=False, has_media=False, has_call=False)
    assert main_win.clock_widget.isVisible()
    assert main_win.media_widget.isHidden()
    assert main_win.nav_widget.isHidden()
    assert main_win.phone_card.isHidden()

    # 2. Media + Phone card
    main_win.update_dashboard_state(has_nav=False, has_media=True, has_call=False)
    assert main_win.media_widget.isVisible()
    assert main_win.phone_card.isVisible()
    assert main_win.nav_widget.isHidden()

    # 3. Media + Nav card
    main_win.update_dashboard_state(has_nav=True, has_media=True, has_call=False)
    assert main_win.media_widget.isVisible()
    assert main_win.nav_widget.isVisible()
    assert main_win.phone_card.isHidden()

    # 4. Media + Call
    main_win.update_dashboard_state(has_nav=False, has_media=True, has_call=True)
    assert main_win.phone_call_widget.isVisible()
    assert main_win.clock_widget.isVisible()


def test_main_window_connection_states(main_win):
    # Connected
    main_win.isVideoFocused = True
    main_win.set_connected_state(True)
    assert main_win._is_connected is True
    assert main_win.disconnected_screen.isHidden()

    # Disconnected
    main_win.set_connected_state(False, is_disconnect=True)
    assert main_win._is_connected is False
    assert main_win.disconnected_screen.isVisible()


def test_main_window_wifi_restart(main_win):
    with patch("urllib.request.urlopen"):
        with patch.object(main_win.toast_widget, "show_toast") as m_toast:
            main_win._on_wifi_restart()
            m_toast.assert_called_with("Restarting WiFi Hotspot AP...", "info")


def test_video_viewport_margins_and_callbacks(app):
    viewport = VideoViewportWidget()
    viewport.resize(800, 480)

    # Margins
    viewport.set_margins(10, 20, stretch_to_fill=False)
    assert viewport.margin_width == 10
    assert viewport.margin_height == 20
    assert viewport.stretch_to_fill is False

    # Bound callback
    cb_called = []
    viewport.set_sink_bound_callback(lambda: cb_called.append(True))
    viewport._attached = True
    viewport.set_sink_bound_callback(lambda: cb_called.append(True))
    assert len(cb_called) > 0

    # Attach GL decoder
    mock_dec = MagicMock()
    mock_dec.is_available = True
    viewport.attach_gl_decoder(mock_dec)
    mock_dec.attach_viewport.assert_called_with(viewport)

    # Attach GStreamer sink
    viewport.attach_gstreamer_sink("mock_sink")
    assert viewport._gst_sink == "mock_sink"

    # Cleanup GL
    viewport.cleanupGL()
    assert viewport._gst_sink is None
    assert viewport._attached is False


def test_video_viewport_update_frame_fallback(app):
    viewport = VideoViewportWidget()
    viewport.resize(100, 100)

    # Frame too short: ignored
    viewport.update_frame(b"\x00" * 10, 100, 100)
    assert viewport.current_frame_data is None

    # Valid frame (100 * 100 * 4 = 40,000 bytes)
    frame_bytes = b"\xFF\x00\x00\xFF" * 10000
    viewport.update_frame(frame_bytes, 100, 100)
    assert viewport.current_frame_data == frame_bytes
    assert viewport._image_provider.image is not None


def test_video_viewport_mouse_events(app):
    viewport = VideoViewportWidget()
    viewport.resize(800, 480)
    viewport.frame_width = 800
    viewport.frame_height = 480

    touches = []
    user_inputs = []
    viewport.touch_input_event.connect(lambda t: touches.append(t))
    viewport.user_input_event.connect(lambda *args: user_inputs.append(args))

    pos = QPointF(200.0, 150.0)
    press_ev = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        pos,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    viewport.mousePressEvent(press_ev)
    assert len(touches) == 1
    assert touches[-1]["action"] == 0
    assert touches[-1]["pointers"][0]["x"] == 200
    assert user_inputs[-1][0] == "press"

    # Mouse move with button
    move_ev = QMouseEvent(
        QEvent.Type.MouseMove,
        pos,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    viewport.sample_interval_ms = 0
    viewport.mouseMoveEvent(move_ev)
    assert len(touches) == 2
    assert touches[-1]["action"] == 2
    assert user_inputs[-1][0] == "move"

    # Mouse release
    release_ev = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        pos,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    viewport.mouseReleaseEvent(release_ev)
    assert len(touches) == 3
    assert touches[-1]["action"] == 1
    assert user_inputs[-1][0] == "release"


def test_video_viewport_qml_status_and_errors(app):
    viewport = VideoViewportWidget()
    viewport._on_scenegraph_error(1, "Test error")

    # QML status error
    from PyQt6.QtQuickWidgets import QQuickWidget
    viewport._is_fallback_mode = False
    with patch.object(viewport, "errors", return_value=[]), \
         patch.object(viewport, "_load_fallback_qml") as m_fallback:
        viewport._on_qml_status_changed(QQuickWidget.Status.Error)
        m_fallback.assert_called_once()


def test_video_viewport_touch_cancel(app):
    viewport = VideoViewportWidget()
    viewport.resize(800, 480)
    touches = []
    viewport.touch_input_event.connect(lambda t: touches.append(t))

    ev = QEvent(QEvent.Type.TouchCancel)
    assert viewport.event(ev) is True
    assert len(touches) == 1
    assert touches[0]["action"] == 1
