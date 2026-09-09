"""
test_qt6_gui_module.py — Comprehensive Unit Tests for Qt6GuiModule and _QtSignalBridge
(backend.modules.qt6_gui.main)
"""

import asyncio
import sys
from unittest.mock import MagicMock, patch, AsyncMock
import pytest
from aiohttp import web

from backend.modules.qt6_gui.main import (
    Qt6GuiModule,
    GuiEventBridge,
    _qt_diagnostic_message_handler,
    HAS_PYQT6,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_gui_module():
    with patch("shared.base_module.ConfigClient"), \
         patch("shared.base_module.BusClient"):
        mod = Qt6GuiModule()
        mod.log = MagicMock()
        mod.publish = MagicMock()
        mod.main_window = MagicMock()
        mod.shm_engine = MagicMock()
        mod.audio_engine = MagicMock()
        return mod


def test_qt_diagnostic_message_handler():
    context = MagicMock()
    context.file = "test.py"
    context.line = 42
    context.function = "test_fn"

    from PyQt6.QtCore import QtMsgType
    # Test QBasicTimer warning
    _qt_diagnostic_message_handler(QtMsgType.QtWarningMsg, context, "QBasicTimer::start: Timers cannot be started")
    # Test normal warning
    _qt_diagnostic_message_handler(QtMsgType.QtWarningMsg, context, "General Qt warning")
    # Test critical error
    _qt_diagnostic_message_handler(QtMsgType.QtCriticalMsg, context, "Critical Qt error")
    # Test exception safety (None context)
    _qt_diagnostic_message_handler(QtMsgType.QtFatalMsg, None, "Fatal error")


def test_qt6_gui_module_config_and_schema(mock_gui_module):
    defaults = mock_gui_module.get_default_config()
    assert defaults["fullscreen"] is True
    assert defaults["theme"] == "dark"
    assert defaults["enable_mic"] is True

    schema = mock_gui_module.get_schema()
    assert "fullscreen" in schema
    assert "theme" in schema
    assert "enable_mic" in schema

    # Config updated fullscreen change
    mock_gui_module.on_config_updated({"fullscreen": False})
    mock_gui_module.main_window.fullscreen_change_requested.emit.assert_called_with(False)


def test_qt_signal_bridge_slots(mock_gui_module):
    bridge = GuiEventBridge(mock_gui_module)
    data = {"sample": 123}

    with patch.object(mock_gui_module, "_on_connectivity_status_updated") as m:
        bridge.on_connectivity_updated(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_channel_status_updated") as m:
        bridge.on_channel_status_updated(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_shm_video_notify") as m:
        bridge.on_shm_video_notify(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_shm_audio_notify") as m:
        bridge.on_shm_audio_notify(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_audio_channel_configured") as m:
        bridge.on_audio_channel_configured(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_audio_sink_changed") as m:
        bridge.on_audio_sink_changed(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_mic_control_notify") as m:
        bridge.on_mic_control_notify(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_nav_turn_notify") as m:
        bridge.on_nav_turn_notify(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_nav_dist_notify") as m:
        bridge.on_nav_dist_notify(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_media_metadata_notify") as m:
        bridge.on_media_metadata_notify(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_media_playback_status_notify") as m:
        bridge.on_media_playback_status_notify(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_phone_status_notify") as m:
        bridge.on_phone_status_notify(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_phone_pbap_synced") as m:
        bridge.on_phone_pbap_synced(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_audio_focus_notify") as m:
        bridge.on_audio_focus_notify(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_notification_post_notify") as m:
        bridge.on_notification_post(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_notification_dismiss_notify") as m:
        bridge.on_notification_dismiss(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_media_tree_updated_notify") as m:
        bridge.on_media_tree_updated(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_diagnostic_audio_test") as m:
        bridge.on_diagnostic_audio_test(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_audio_stream_status") as m:
        bridge.on_audio_stream_status(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_bt_device_connected") as m:
        bridge.on_bt_device_connected(data)
        m.assert_called_once_with(data)

    with patch.object(mock_gui_module, "_on_bt_device_disconnected") as m:
        bridge.on_bt_device_disconnected(data)
        m.assert_called_once_with(data)


def test_on_connectivity_status_updated(mock_gui_module):
    mock_gui_module.main_window._is_connected = False
    
    # Online transition
    data_online = {"stage_index": 10, "toast_message": "Ready"}
    mock_gui_module._on_connectivity_status_updated(data_online)
    mock_gui_module.main_window.command_bar.set_online_status.assert_called_with(True)
    mock_gui_module.main_window.set_connected_state.assert_called_with(True)
    mock_gui_module.main_window.toast_widget.show_toast.assert_called_with("Ready", "success")

    # Pairing PIN request
    data_pin = {"pairing_pin": "123456", "pairing_device": "Pixel 9"}
    mock_gui_module._on_connectivity_status_updated(data_pin)
    mock_gui_module.main_window.toast_widget.show_toast.assert_called_with(
        "🔑 Pairing Request from Pixel 9: PIN 123456", "warning"
    )


def test_on_video_focus_toggled(mock_gui_module):
    mock_gui_module._on_video_focus_toggled("PROJECTED")
    mock_gui_module.shm_engine.set_video_focused.assert_called_with(True)
    mock_gui_module.publish.assert_called_with("media.video.request_focus", {"mode": "PROJECTED", "sender": "qt6_gui"})


def test_on_channel_status_updated(mock_gui_module):
    mock_gui_module._on_channel_status_updated({"connected": True})
    mock_gui_module.main_window.command_bar.set_online_status.assert_called_with(True)
    mock_gui_module.main_window.set_connected_state.assert_called_with(True)


def test_on_shm_video_audio_notify(mock_gui_module):
    mock_gui_module._on_shm_video_notify({"shm_offset": 1024, "channel_id": 1})
    mock_gui_module.shm_engine.process_downstream_video.assert_called_with(1024, channel_id=1)

    mock_gui_module._on_shm_audio_notify({"shm_offset": 2048, "channel_id": 3})
    mock_gui_module.shm_engine.process_downstream_audio.assert_called_with(2048, channel_id=3)


def test_on_audio_channel_configured_and_sink(mock_gui_module):
    mock_gui_module._on_audio_channel_configured({
        "channel_id": 3,
        "codec": "MEDIA_CODEC_AUDIO_AAC",
        "sample_rate": 44100,
        "channel_count": 2,
        "bit_depth": 16,
    })
    mock_gui_module.audio_engine.configure_channel_codec.assert_called_with(
        channel_id=3,
        codec="MEDIA_CODEC_AUDIO_AAC",
        sample_rate=44100,
        channel_count=2,
        bit_depth=16,
    )

    mock_gui_module._on_audio_sink_changed({"sink": "alsa_output.pci", "source": "alsa_input.pci"})
    mock_gui_module.audio_engine.set_output_sink.assert_called_with("alsa_output.pci")
    mock_gui_module.audio_engine.set_input_source.assert_called_with("alsa_input.pci")

    mock_gui_module._on_audio_stream_status({"channel_id": 3, "status": "START"})
    mock_gui_module.audio_engine.set_stream_status.assert_called_with("START", channel_id=3)


def test_on_nav_notifications(mock_gui_module):
    mock_gui_module.main_window.has_active_media = True
    
    # Nav turn
    mock_gui_module._on_nav_turn_notify({
        "road": "Main St",
        "distance_meters": 250.0,
        "maneuver_type": 1,
        "turn_side": 2,
        "turn_icon": "base64icon",
    })
    mock_gui_module.main_window.nav_widget.update_navigation.assert_called_with(
        road="Main St",
        distance_meters=250.0,
        maneuver_type=1,
        turn_side=2,
        turn_icon_b64="base64icon",
    )
    mock_gui_module.main_window.update_dashboard_state.assert_called_with(has_nav=True, has_media=True)

    # Nav distance update
    mock_gui_module._on_nav_dist_notify({
        "distance_meters": 100.0,
        "road": "Main St",
        "eta_seconds": 120,
        "maneuver_type": 1,
        "turn_side": 2,
    })
    mock_gui_module.main_window.nav_widget.update_navigation.assert_called_with(
        road="Main St",
        distance_meters=100.0,
        maneuver_type=1,
        turn_side=2,
        eta_seconds=120,
    )


def test_on_media_notifications(mock_gui_module):
    mock_gui_module.main_window.has_active_nav = False
    
    # Metadata notify
    mock_gui_module._on_media_metadata_notify({
        "title": "Song Title",
        "artist": "Artist Name",
        "album": "Album Title",
        "album_art": "art_bytes",
    })
    mock_gui_module.main_window.media_widget.update_metadata.assert_called_with(
        title="Song Title",
        artist="Artist Name",
        album="Album Title",
        album_art_b64="art_bytes",
    )
    mock_gui_module.main_window.update_dashboard_state.assert_called_with(has_nav=False, has_media=True)

    # Playback status: playing (Android Auto proto: 2 = PLAYING)
    mock_gui_module._on_media_playback_status_notify({
        "playback_state": 2,
        "media_source": "Spotify",
        "position_seconds": 45,
    })
    mock_gui_module.audio_engine.set_paused.assert_called_with(False)
    mock_gui_module.main_window.command_bar.update_playback_state.assert_called_with(True)

    # Playback status: paused (Android Auto proto: 3 = PAUSED)
    mock_gui_module._on_media_playback_status_notify({
        "playback_state": 3,
        "media_source": "Spotify",
        "position_seconds": 50,
    })
    mock_gui_module.audio_engine.set_paused.assert_called_with(True)
    mock_gui_module.main_window.command_bar.update_playback_state.assert_called_with(False)

    # Audio focus notify
    mock_gui_module._on_audio_focus_notify({"is_paused": True, "channel_id": 3})
    mock_gui_module.audio_engine.set_paused.assert_called_with(True, channel_id=3)


def test_on_phone_status_notify(mock_gui_module):
    mock_gui_module.main_window.has_active_nav = False
    mock_gui_module.main_window.has_active_media = False
    
    # Telemetry only
    mock_gui_module._on_phone_status_notify({
        "signal_strength": 4,
        "battery_level": 85,
        "is_charging": True,
        "operator_name": "Carrier",
        "device_name": "My Phone",
    })
    mock_gui_module.main_window.command_bar.update_phone_status.assert_called_with(
        signal=4,
        battery=85,
        is_charging=True,
        operator_name="Carrier",
        is_roaming=False,
        is_connected=True,
    )
    mock_gui_module.main_window.phone_card.update_telemetry.assert_called_with(
        device_name="My Phone",
        carrier="Carrier",
        signal_bars=4,
        battery_pct=85,
        is_connected=True,
    )

    # Call state ringing
    mock_gui_module._on_phone_status_notify({
        "is_in_call": True,
        "call_state": "RINGING",
        "caller_name": "Alice",
        "caller_number": "555-1234",
        "call_duration_seconds": 0,
    })
    mock_gui_module.main_window.phone_call_widget.update_call_state.assert_called_with(
        is_in_call=True,
        call_state="RINGING",
        caller_name="Alice",
        caller_number="555-1234",
        duration_seconds=0,
        contact_photo_b64="",
    )
    mock_gui_module.main_window.update_dashboard_state.assert_called_with(
        has_nav=False, has_media=False, has_call=True
    )
    mock_gui_module.main_window.disconnected_screen.show.assert_called()


def test_on_phone_pbap_synced_and_bt_connections(mock_gui_module):
    contacts = [{"name": "Bob", "primary_phone": "12345"}]
    favorites = [{"name": "Alice", "primary_phone": "67890"}]
    recents = [{"name": "Charlie", "number": "11111"}]

    mock_gui_module._on_phone_pbap_synced({
        "contacts": contacts,
        "favorites": favorites,
        "recents": recents,
    })
    mock_gui_module.main_window.phone_drawer.set_contacts.assert_called_with(contacts)
    mock_gui_module.main_window.phone_card.set_quick_contact.assert_called_with("Alice", "67890")
    mock_gui_module.main_window.toast_widget.show_toast.assert_called()

    # BT connected / disconnected
    mock_gui_module._on_bt_device_connected({"device_address": "AA:BB:CC:DD:EE:FF", "device_name": "TestPhone"})
    mock_gui_module.main_window.phone_card.update_telemetry.assert_called_with(
        device_name="TestPhone",
        is_connected=True,
    )

    mock_gui_module._on_bt_device_disconnected({"device_address": "AA:BB:CC:DD:EE:FF"})
    mock_gui_module.main_window.phone_card.update_telemetry.assert_called_with(
        device_name="",
        is_connected=False,
    )


@pytest.mark.asyncio
async def test_hydrate_initial_bt_state(mock_gui_module):
    mock_gui_module.call_module = AsyncMock(side_effect=[
        {"status": "ok", "active_device": "AA:BB:CC:DD:EE:FF"},
        {"status": "ok", "devices": [{"address": "AA:BB:CC:DD:EE:FF", "name": "HydratedPhone"}]},
    ])

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await mock_gui_module._hydrate_initial_bt_state()

    mock_gui_module.main_window.phone_card.update_telemetry.assert_called_with(
        device_name="HydratedPhone",
        is_connected=True,
    )


def test_notifications_and_media_tree(mock_gui_module):
    mock_gui_module._on_notification_post_notify({
        "id": "notif-1",
        "app_name": "Messages",
        "title": "New text",
        "text": "Hello world",
    })
    mock_gui_module.main_window.notification_toast.show_notification.assert_called_with(
        notif_id="notif-1",
        title="New text",
        text="Hello world",
        app_name="Messages",
    )
    mock_gui_module.main_window.notification_card.add_notification.assert_called_with(
        notif_id="notif-1",
        title="New text",
        text="Hello world",
        app_name="Messages",
    )

    mock_gui_module._on_notification_dismiss_notify({"id": "notif-1"})
    mock_gui_module.main_window.notification_card.remove_notification.assert_called_with("notif-1")

    mock_gui_module._on_media_tree_updated_notify({
        "path": "/root",
        "items": [{"id": "track-1", "title": "Track 1"}],
    })
    mock_gui_module.main_window.media_widget.set_browser_items.assert_called_with(
        "/root", [{"id": "track-1", "title": "Track 1"}]
    )


@pytest.mark.asyncio
async def test_send_phone_action_and_media_keys(mock_gui_module):
    mock_resp = MagicMock()
    mock_resp.status = 200

    with patch("urllib.request.urlopen", return_value=mock_resp):
        await mock_gui_module._send_phone_action("dial:12345")
        await mock_gui_module._send_phone_action("dtmf:5")
        await mock_gui_module._send_phone_action("sync")
        await mock_gui_module._send_phone_action("hangup")
        await mock_gui_module._send_media_key(85)

    mock_gui_module.log.info.assert_called()


def test_mic_control_and_upstream_mic(mock_gui_module):
    mock_gui_module.config["enable_mic"] = True
    mock_gui_module._on_mic_control_notify({"enabled": True})
    mock_gui_module.audio_engine.start_microphone.assert_called_once()

    mock_gui_module._on_mic_control_notify({"enabled": False})
    mock_gui_module.audio_engine.stop_microphone.assert_called_once()

    mock_gui_module.shm_engine.write_upstream_mic.return_value = 512
    mock_gui_module._on_mic_data_captured(b"pcmchunk")
    mock_gui_module.publish.assert_called_with("media.audio.mic_shm", {"shm_offset": 512, "len": 8})


@pytest.mark.asyncio
async def test_stream_start_stop(mock_gui_module):
    await mock_gui_module._on_stream_start({})
    mock_gui_module.shm_engine.set_video_focused.assert_called_with(True)
    assert mock_gui_module.main_window.isVideoFocused is True
    mock_gui_module.publish.assert_called_with("media.video.request_focus", {"sender": "qt6_gui"})

    await mock_gui_module._on_stream_stop({})
    mock_gui_module.shm_engine.set_video_focused.assert_called_with(False)


def test_video_and_audio_shm_frames(mock_gui_module):
    mock_gui_module.main_window.isVideoFocused = True
    mock_gui_module.main_window.disconnected_screen.isVisible.return_value = True

    # Frame 1: sets baseline
    mock_gui_module._on_video_frame_from_shm(b"\x00" * 100, 800, 480, 1_000_000)
    mock_gui_module.main_window.disconnected_screen.hide.assert_called()
    mock_gui_module.main_window.video_viewport.update_frame.assert_called_with(b"\x00" * 100, 800, 480)

    # Frame 2: calculates lag
    mock_gui_module._on_video_frame_from_shm(b"\x00" * 100, 800, 480, 1_033_000)

    # Audio frame
    mock_gui_module._on_audio_frame_from_shm(b"\x01\x02", 3, 1_000_000)
    mock_gui_module.audio_engine.play_pcm_frame.assert_called_with(b"\x01\x02", channel_id=3, ts_us=1_000_000)


def test_update_audio_buffer_status(mock_gui_module):
    mock_gui_module.audio_engine.get_metrics.return_value = {
        3: {
            "is_started": True,
            "channel_id": 3,
            "total_bytes_in": 1000,
            "lag_ms": 25,
            "app_buffer": {"buffered_ms": 20, "underruns": 0},
            "sink_buffer": {"queued_ms": 15},
        }
    }
    mock_gui_module._last_stats_log_time = 0.0
    mock_gui_module._video_fps = 30.0
    mock_gui_module._video_lag_ms = 40.0

    mock_gui_module._update_audio_buffer_status()
    mock_gui_module.main_window.command_bar.update_audio_status.assert_called()


def test_touch_and_user_input_and_close(mock_gui_module):
    # Touch input
    mock_gui_module._on_touch_input_event({"action": 0, "pointers": [{"x": 100, "y": 200}]})
    mock_gui_module.publish.assert_called_with("input.event", {"action": 0, "pointers": [{"x": 100, "y": 200}]})

    # User input
    mock_gui_module._on_user_input_event("mouse_move", 50, 60, 0)
    mock_gui_module.publish.assert_called_with("input.event", {"type": "mouse_move", "x": 50, "y": 60, "button": 0})

    # Close requested
    mock_gui_module._on_close_requested()
    assert mock_gui_module._running is False
    mock_gui_module.publish.assert_any_call("system.shutdown", {"sender": "qt6_gui", "reason": "gui_close"})
    mock_gui_module.audio_engine.close.assert_called()
    mock_gui_module.shm_engine.close.assert_called()


@pytest.mark.asyncio
async def test_handle_get_status_and_teardown(mock_gui_module):
    pill = MagicMock()
    pill._battery = 80
    pill._signal = 4
    pill.lbl_battery.text.return_value = "80%"
    pill.toolTip.return_value = "Connected"

    phone_card = MagicMock()
    phone_card._device_name = "Pixel"
    phone_card._signal_bars = 4
    phone_card._battery_pct = 80
    phone_card.lbl_signal.text.return_value = "4G"
    phone_card.lbl_battery.text.return_value = "80%"
    phone_card.lbl_signal.isVisible.return_value = True
    phone_card.lbl_battery.isVisible.return_value = True

    mock_gui_module.main_window.command_bar.phone_pill = pill
    mock_gui_module.main_window.phone_card = phone_card

    req = MagicMock(spec=web.Request)
    resp = await mock_gui_module.handle_get_status(req)
    assert resp.status == 200

    # Teardown
    mock_gui_module.vol_listener = MagicMock()
    await mock_gui_module.teardown()
    mock_gui_module.vol_listener.stop.assert_called_once()
    mock_gui_module.publish.assert_any_call("system.shutdown", {"sender": "qt6_gui", "reason": "gui_teardown"})


@pytest.mark.asyncio
async def test_qt6_gui_module_setup_and_run(mock_gui_module):
    mock_win = MagicMock()
    mock_shm = MagicMock()
    mock_audio = MagicMock()
    mock_vol = MagicMock()
    mock_timer = MagicMock()

    with patch("backend.modules.qt6_gui.main.MainWindow", return_value=mock_win), \
         patch("backend.modules.qt6_gui.main.QtSHMMediaEngine", return_value=mock_shm), \
         patch("backend.modules.qt6_gui.main.QtAudioEngine", return_value=mock_audio), \
         patch("backend.modules.qt6_gui.main.HardwareVolumeListener", return_value=mock_vol), \
         patch("backend.modules.qt6_gui.main.dismiss_boot_splash"), \
         patch("backend.modules.qt6_gui.main.QTimer", return_value=mock_timer), \
         patch.object(mock_gui_module, "_hydrate_initial_bt_state", return_value=None):
        mock_gui_module.app = MagicMock()
        await mock_gui_module.setup()
        assert mock_gui_module.main_window == mock_win
        assert mock_gui_module.shm_engine == mock_shm
        assert mock_gui_module.audio_engine == mock_audio
        assert mock_gui_module.vol_listener == mock_vol
        mock_vol.start.assert_called_once()
        mock_gui_module.publish.assert_called_with("media.video.request_focus", {"sender": "qt6_gui"})

    # Test run() single iteration
    mock_gui_module._running = True
    async def stop_soon():
        await asyncio.sleep(0.02)
        mock_gui_module._running = False

    asyncio.create_task(stop_soon())
    await mock_gui_module.run()
    assert mock_gui_module._running is False


def test_diagnostic_audio_test(mock_gui_module):
    mock_cli = MagicMock()
    with patch.dict(sys.modules, {"test_audio_cli": mock_cli}):
        mock_gui_module._on_diagnostic_audio_test({
            "freq": 880.0,
            "duration": 1.0,
            "push": True,
            "device": "default",
        })
        mock_cli.test_qaudiosink.assert_called_once_with("default", 880.0, 1.0, True)

    # Invalid non-dict payload
    mock_gui_module._on_diagnostic_audio_test("not_a_dict")


def test_phone_pbap_edge_cases(mock_gui_module):
    # Only recents
    mock_gui_module._on_phone_pbap_synced({
        "contacts": [],
        "favorites": [],
        "recents": [{"name": "Recent Person", "number": "123"}],
    })
    mock_gui_module.main_window.phone_card.set_quick_contact.assert_called_with("Recent Person", "123")

    # Only contacts
    mock_gui_module._on_phone_pbap_synced({
        "contacts": [{"name": "Contact Person", "primary_phone": "456"}],
        "favorites": [],
        "recents": [],
    })
    mock_gui_module.main_window.phone_card.set_quick_contact.assert_called_with("Contact Person", "456")


def test_video_frame_pts_jump(mock_gui_module):
    mock_gui_module._video_first_sys_time = 100.0
    mock_gui_module._video_first_ts_us = 100_000_000

    # Huge PTS jump forward (e.g. 10s difference) triggers baseline reset
    mock_gui_module._on_video_frame_from_shm(b"\x00" * 10, 100, 100, 120_000_000)
    assert mock_gui_module._video_lag_ms == 0.0


@pytest.mark.asyncio
async def test_phone_and_media_action_triggers(mock_gui_module):
    with patch.object(mock_gui_module, "_send_phone_action", new_callable=AsyncMock) as m_phone, \
         patch.object(mock_gui_module, "_send_media_key", new_callable=AsyncMock) as m_media:
        mock_gui_module._on_phone_action_requested("hangup")
        mock_gui_module._on_media_playpause_requested()
        mock_gui_module._on_media_action_requested(87)

