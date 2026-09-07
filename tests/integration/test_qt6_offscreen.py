# tests/integration/test_qt6_offscreen.py
import pytest
import os
import sys

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def main_window():
    """Create a single MainWindow instance offscreen for the module test session."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication
    from backend.modules.qt6_gui.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    yield window
    window.close()


def test_qt6_gui_offscreen_window_hierarchy(main_window):
    """Verify MainWindow loads cleanly offscreen with correct component hierarchy."""
    assert main_window is not None
    assert "Wireless Android Auto" in main_window.windowTitle()
    assert main_window.video_viewport is not None
    assert main_window.phone_card is not None
    assert main_window.command_bar is not None
    assert main_window.clock_widget is not None
    assert main_window.media_widget is not None


def test_qt6_gui_offscreen_phone_telemetry_reactivity(main_window):
    """Verify PhoneCardWidget updates its visual state and telemetry labels on phone status updates."""
    # Simulate connection with telemetry
    main_window.phone_card.update_telemetry(
        is_connected=True,
        device_name="Pixel 8 Pro",
        carrier="T-Mobile",
        signal_bars=4,
        battery_pct=85,
    )

    assert main_window.phone_card._is_connected is True
    assert main_window.phone_card.lbl_status_pill.text() == "CONNECTED"
    assert "Pixel 8 Pro" in main_window.phone_card.lbl_device.text()
    assert main_window.phone_card.lbl_carrier.text() == "T-Mobile"
    assert main_window.phone_card.lbl_signal.text() == "4/5"
    assert main_window.phone_card.lbl_battery.text() == "85%"

    # Simulate disconnection
    main_window.phone_card.update_telemetry(is_connected=False)
    assert main_window.phone_card._is_connected is False
    assert main_window.phone_card.lbl_status_pill.text() == "SEARCHING"


def test_qt6_gui_offscreen_video_focus_and_command_bar(main_window):
    """Verify video focus toggling and command bar state updates in offscreen environment."""
    # Initial state is True
    assert main_window.isVideoFocused is True

    # Toggle to NATIVE (clock overlay shown)
    main_window._toggle_clock_overlay()
    assert main_window.isVideoFocused is False

    # Toggle back to PROJECTED
    main_window._toggle_clock_overlay()
    assert main_window.isVideoFocused is True

    # Command bar updates
    main_window.command_bar.update_phone_status(
        signal=5,
        battery=90,
        is_charging=True,
        operator_name="Vodafone",
        is_roaming=False,
        is_connected=True,
    )

    assert main_window.command_bar.phone_pill._signal == 5
    assert main_window.command_bar.phone_pill._battery == 90
    assert main_window.command_bar.phone_pill.lbl_battery.text() == "90%"
    assert main_window.command_bar.phone_pill._is_charging is True


def test_qt6_gui_offscreen_video_viewport_touch_signals(main_window):
    """Verify VideoViewportWidget generates and emits structured touch input events."""
    received_events = []
    main_window.video_viewport.touch_input_event.connect(lambda ev: received_events.append(ev))

    # Emit touch event directly
    test_event = {
        "type": "press",
        "action": 0,
        "x": 640,
        "y": 360,
        "pointer_id": 0,
    }
    main_window.video_viewport.touch_input_event.emit(test_event)

    assert len(received_events) == 1
    assert received_events[0]["type"] == "press"
    assert received_events[0]["x"] == 640
    assert received_events[0]["y"] == 360
