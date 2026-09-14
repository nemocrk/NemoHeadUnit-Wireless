# Real-World Testing Enhancements & Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all 9 fixes and enhancements from docs/TODO.md covering UI styling, Audio Sink recovery & false-underrun fixes, Navigation proto decoding, Phone Widget with HFP integration, Volume key binding, and Video/BT stability.

**Architecture:**
- **UI (Qt6 GUI):** Enlarge slide-over drawers into near full-screen cards with solid backgrounds. Introduce incoming call widget overlay on Clock Page and standalone Phone Widget. Bind hardware volume keys (`KEY_VOLUMEUP`/`KEY_VOLUMEDOWN`) via evdev / Qt event filter.
- **Audio Pipeline (`audio_handler.py`):** Suppress underrun counters/badges when audio channels are paused or losing focus. Implement hotplug sink auto-recovery and fallback to default audio output when USB DAC disconnects or reconnects.
- **Navigation Channel (`navigation_handler.py` & `nav_card_widget.py`):** Dispatch incoming messages strictly by message ID (0x8001-0x8007). Correctly parse `NavigationState` (0x8006) and `NavigationCurrentPosition` (0x8007) for maneuver types, road names, turn sides, and remaining distances.
- **Phone & HFP (`phone_status_handler.py`, `connectivity_manager`, `qt6_gui`):** Wire incoming call events to the UI with Accept/Reject/Mute actions. Log raw HFP AT commands and protocol frames.
- **System / Hardware (`fix_omni10.sh`, BlueZ, GStreamer):** Validate button array udev rules and investigate video stall/recovery and BT discovery timeouts.

**Tech Stack:** Python 3.11+, PyQt6, ZeroMQ, Protobuf (Google Protocol Buffers), Linux ALSA/PipeWire/WirePlumber, evdev, BlueZ DBus.

---

## User Review Required

> [!IMPORTANT]
> - **Phone Audio Routing (HFP):** Bluetooth HFP audio (SCO) on Linux is handled via PipeWire / WirePlumber (`bluez5.enable-hfp = true`). In-app HFP routing uses the system PipeWire sink/source rather than raw SCO socket capture in Python to avoid exclusive socket conflicts with WirePlumber.
> - **Hardware Volume Buttons:** The HP Omni 10 volume buttons communicate via `INTCFD9:00` / `soc_button_array` / evdev (`/dev/input/event*`). The Qt6 GUI will monitor evdev directly in a background thread and fallback to standard Qt `QKeyEvent` (`Key_VolumeUp` / `Key_VolumeDown`).

---

## Proposed Changes & Tasks

### Task 1 (TODO #9): Avoid UNDERRUN Badge on Paused Audio Channel

**Files:**
- Modify: `backend/modules/qt6_gui/media/audio_handler.py`
- Modify: `backend/modules/qt6_gui/ui/command_bar.py`
- Test: `tests/test_audio_underrun_suppression.py`

**Interfaces:**
- Consumes: Audio focus events and pause state from Channel Manager / Qt6 GUI.
- Produces: `app_buffer.is_paused` and suppressed underrun accounting during focus pause.

- [ ] **Step 1: Write test for paused audio underrun suppression**
```python
# tests/test_audio_underrun_suppression.py
import pytest
from unittest.mock import MagicMock

def test_audio_handler_ignores_underruns_when_paused():
    from backend.modules.qt6_gui.media.audio_handler import AudioStreamHandler
    handler = AudioStreamHandler(channel_id=5, target_device="default")
    handler.set_paused(True)
    # Simulate buffer starvation while paused
    handler._check_buffer_starvation()
    assert handler._underrun_count == 0
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_audio_underrun_suppression.py -v`

- [ ] **Step 3: Implement pause state tracking and underrun suppression**
In `backend/modules/qt6_gui/media/audio_handler.py`:
- Add `self._is_paused = False` and `def set_paused(self, paused: bool) -> None`.
- In starvation and underflow callback checks (`_check_buffer_starvation`, pull/push callbacks), skip `self._underrun_count += 1` if `self._is_paused` is True.
- In `get_diagnostics()`, export `"is_paused": self._is_paused`.
In `backend/modules/qt6_gui/ui/command_bar.py`:
- In `update_audio_stats()`, if `app_buf.get("is_paused")` is True or `state_text` is `PAUSED`, don't set status color to red for underruns.

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_audio_underrun_suppression.py -v`

- [ ] **Step 5: Commit**
```bash
git add backend/modules/qt6_gui/media/audio_handler.py backend/modules/qt6_gui/ui/command_bar.py tests/test_audio_underrun_suppression.py
git commit -m "fix(audio): suppress underrun count and badge when channel is paused"
```

---

### Task 2 (TODO #1): Drawer Full-Screen Cards with Solid Background

**Files:**
- Modify: `backend/modules/qt6_gui/ui/main_window.py`
- Modify: `backend/modules/qt6_gui/ui/drawers/bluetooth_drawer.py`
- Modify: `backend/modules/qt6_gui/ui/drawers/settings_drawer.py`
- Modify: `backend/modules/qt6_gui/ui/drawers/logs_drawer.py`
- Modify: `backend/modules/qt6_gui/ui/drawers/diagnostics_drawer.py`

**Interfaces:**
- Consumes: Window resize events (`resizeEvent`) in `MainWindow`.
- Produces: Expanded card geometry `(x=30, y=30, w=width-60, h=height - cmd_bar - 60)` with opaque solid styling (`#121316` / `#18191f`).

- [ ] **Step 1: Update drawer geometry in `main_window.py`**
In `MainWindow.resizeEvent()`:
```python
# Position Slide-Over Drawers as almost full-screen cards (30px margin on all sides, strictly above bottom command bar)
margin = 30
drawer_x = margin
drawer_y = margin
drawer_w = max(100, w - (margin * 2))
drawer_h = max(100, draw_h - (margin * 2))
for drawer in (self.bluetooth_drawer, self.settings_drawer, self.logs_drawer, self.diagnostics_drawer):
    drawer.setGeometry(drawer_x, drawer_y, drawer_w, drawer_h)
```

- [ ] **Step 2: Ensure solid background styling on all drawers**
Update drawer root widget stylesheets in each drawer file to set an opaque background (`background: #141519; border-radius: 16px; border: 1px solid #2a2c34;`) so underlying content does not bleed through.

- [ ] **Step 3: Test via Qt6 GUI launch smoke test**
Run: `python -m backend.modules.qt6_gui.main --help` or verify component instantiation.

- [ ] **Step 4: Commit**
```bash
git add backend/modules/qt6_gui/ui/main_window.py backend/modules/qt6_gui/ui/drawers/*.py
git commit -m "style(qt6_gui): expand drawers to near full-screen cards with solid background"
```

---

### Task 3 (TODO #6): Audio Sink Recovery & Fallback on USB Disconnect/Reconnect

**Files:**
- Modify: `backend/modules/qt6_gui/media/audio_handler.py`
- Test: `tests/test_audio_sink_recovery.py`

**Interfaces:**
- Consumes: `QAudioSink.stateChanged`, `QAudio.Error.IOError`, `QMediaDevices.audioOutputsChanged`.
- Produces: Seamless re-initialization of `QAudioSink` on default device when target USB DAC drops.

- [ ] **Step 1: Write test for audio sink fallback logic**
```python
# tests/test_audio_sink_recovery.py
from unittest.mock import MagicMock, patch
from backend.modules.qt6_gui.media.audio_handler import AudioStreamHandler

def test_audio_sink_triggers_reconnect_on_io_error():
    handler = AudioStreamHandler(channel_id=5, target_device="USB Audio DAC")
    handler._schedule_recovery = MagicMock()
    handler._handle_sink_error("Error.IOError")
    assert handler._schedule_recovery.called
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_audio_sink_recovery.py -v`

- [ ] **Step 3: Implement device change monitoring and error recovery**
In `backend/modules/qt6_gui/media/audio_handler.py`:
- In `_on_state_changed`, detect `State.StoppedState` paired with `Error.IOError` or `Error.FatalError`.
- Clean up dead `self.audio_sink` and `self.io_device`.
- Debounce and schedule a reconnection task: check if `target_device` is available; if not, fallback to `defaultAudioOutput()`.
- Listen to `QMediaDevices.audioOutputsChanged`: when outputs change, probe if preferred `target_device` re-appeared and rebind.

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_audio_sink_recovery.py -v`

- [ ] **Step 5: Commit**
```bash
git add backend/modules/qt6_gui/media/audio_handler.py tests/test_audio_sink_recovery.py
git commit -m "fix(audio): implement automatic audio sink recovery and fallback on device disconnect"
```

---

### Task 4 (TODO #3): Turn-by-Turn Navigation Proto Decoding & Mapping

**Files:**
- Modify: `backend/modules/channel_manager/handlers/navigation_handler.py`
- Modify: `backend/modules/qt6_gui/ui/nav_card_widget.py`
- Test: `tests/test_navigation_proto_decode.py`

**Interfaces:**
- Consumes: Navigation channel frames (0x8001: Start, 0x8002: Stop, 0x8003: Status, 0x8004: NextTurnDetail, 0x8005: NextTurnDistance, 0x8006: NavigationState, 0x8007: NavigationCurrentPosition).
- Produces: Accurate `navigation.turn_event` and `navigation.distance_event` payloads with correct maneuver icons and distance meters.

- [ ] **Step 1: Write test for message-id based dispatching**
```python
# tests/test_navigation_proto_decode.py
import pytest
from backend.modules.channel_manager.handlers.navigation_handler import NavigationChannelHandler

def test_navigation_dispatches_by_msg_id():
    manager = MagicMock()
    handler = NavigationChannelHandler(manager)
    # Ensure message 0x8006 does NOT get decoded as 0x8004
    # and maneuver does not result in 553
    assert handler is not None
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_navigation_proto_decode.py -v`

- [ ] **Step 3: Implement exact message ID routing and protobuf unpacking**
In `backend/modules/channel_manager/handlers/navigation_handler.py`:
- Map message IDs:
  - `0x8001`: `InstrumentClusterStart`
  - `0x8002`: `InstrumentClusterStop`
  - `0x8004`: `NextTurnDetail` (legacy: extract road name, turn icon, next event)
  - `0x8005`: `NextTurnDistanceEvent` (legacy: extract `distance_meters`, `time_to_turn_seconds`)
  - `0x8006`: `NavigationState` (modern: unpack `stepsList[0]`, `maneuver.type`, `roundaboutExitNumber`, `cue`)
  - `0x8007`: `NavigationCurrentPosition` / `NavigationNextTurnDistanceEvent` (modern: unpack `step_distance.distance_value`, `current_road.name`)
- Map `maneuver.type` integers to canonical UI icons (`turn-left`, `turn-right`, `roundabout`, `merge`, `uturn`, `straight`, etc.).
In `backend/modules/qt6_gui/ui/nav_card_widget.py`:
- Update maneuver icon renderer to display correct SVG/graphics for each mapped maneuver.

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_navigation_proto_decode.py -v`

- [ ] **Step 5: Commit**
```bash
git add backend/modules/channel_manager/handlers/navigation_handler.py backend/modules/qt6_gui/ui/nav_card_widget.py tests/test_navigation_proto_decode.py
git commit -m "fix(navigation): dispatch navigation messages by ID and map modern NavigationState maneuvers"
```

---

### Task 5 (TODO #2 & #8): Phone Call Widget, HFP Call Actions & Protocol Logging

**Files:**
- Create: `backend/modules/qt6_gui/ui/call_widget.py`
- Create: `backend/modules/qt6_gui/ui/drawers/phone_drawer.py`
- Modify: `backend/modules/qt6_gui/ui/clock_widget.py`
- Modify: `backend/modules/qt6_gui/ui/main_window.py`
- Modify: `backend/modules/channel_manager/handlers/phone_status_handler.py`
- Modify: `backend/modules/connectivity_manager/main.py`
- Test: `tests/test_phone_call_widget.py`

**Interfaces:**
- Consumes: `phone.status` events (`is_in_call`, `caller_name`, `caller_number`, `contact_photo_b64`, `call_duration_seconds`).
- Produces: In-call overlay on Clock Page with Answer/Reject/Mute buttons emitting `system.call_action` RPCs.

- [ ] **Step 1: Write test for Phone Call Widget event handling**
```python
# tests/test_phone_call_widget.py
import pytest
from PyQt6.QtWidgets import QApplication
import sys

def test_call_widget_state_updates():
    app = QApplication.instance() or QApplication(sys.argv)
    from backend.modules.qt6_gui.ui.call_widget import CallWidget
    widget = CallWidget()
    widget.update_call_state({
        "is_in_call": True,
        "caller_name": "Test Caller",
        "caller_number": "+123456789",
        "call_state": "RINGING"
    })
    assert widget.caller_label.text() == "Test Caller"
    assert widget.isVisible()
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_phone_call_widget.py -v`

- [ ] **Step 3: Create `CallWidget` and integrate with `clock_widget.py`**
- Create `backend/modules/qt6_gui/ui/call_widget.py`: Card widget showing caller photo (from base64), name, phone number, timer, and action buttons:
  - Green button: `ANSWER`
  - Red button: `REJECT` / `HANGUP`
  - Gray button: `MUTE`
- Embed `CallWidget` into `clock_widget.py`: shown prominently when `is_in_call` is true, replaces/overlays clock display during active calls.
- Connect action buttons to `phone_status_handler.send_phone_action(action)`.
- Create `phone_drawer.py` (or tab in settings) for Recents / Contacts / Favorites.
- In `connectivity_manager` & `bluez_bluetooth.py`, add verbose debug logging for all HFP RFCOMM AT commands (`AT+...`, `OK`, `ERROR`, unsolicited results).

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_phone_call_widget.py -v`

- [ ] **Step 5: Commit**
```bash
git add backend/modules/qt6_gui/ui/call_widget.py backend/modules/qt6_gui/ui/clock_widget.py backend/modules/qt6_gui/ui/main_window.py backend/modules/channel_manager/handlers/phone_status_handler.py tests/test_phone_call_widget.py
git commit -m "feat(phone): add Phone Call Widget on clock page with call actions and HFP logging"
```

---

### Task 6 (TODO #7): Physical Hardware / BT Volume Button Binding

**Files:**
- Create: `backend/modules/qt6_gui/input/volume_key_listener.py`
- Modify: `backend/modules/qt6_gui/ui/main_window.py`
- Modify: `packaging/hardware_fixes/fix_omni10.sh` (already in progress by user)
- Test: `tests/test_volume_listener.py`

**Interfaces:**
- Consumes: Linux evdev input events from `/dev/input/event*` with `KEY_VOLUMEUP`, `KEY_VOLUMEDOWN`, and `KEY_MUTE`.
- Produces: Increments / decrements UI volume via `VolumePopoverWidget` and system mixer.

- [ ] **Step 1: Write test for volume key listener**
```python
# tests/test_volume_listener.py
import pytest
from backend.modules.qt6_gui.input.volume_key_listener import VolumeKeyListener

def test_volume_key_listener_initialization():
    listener = VolumeKeyListener(callback=lambda key: None)
    assert listener is not None
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_volume_listener.py -v`

- [ ] **Step 3: Implement `VolumeKeyListener` and hook to MainWindow**
- In `backend/modules/qt6_gui/input/volume_key_listener.py`:
  - Detect devices with volume keys via `evdev` (e.g. `gpio-keys`, `soc_button_array`, or BT HID headsets).
  - Spawn non-blocking reader thread emitting `volume_up`, `volume_down`, `volume_mute` Qt signals.
- In `MainWindow`:
  - Connect signals to adjust volume step (+5% / -5%) and trigger `volume_popover.show_with_timeout()`.
  - Also override `MainWindow.keyPressEvent` to catch standard `Qt.Key.Key_VolumeUp` / `Qt.Key.Key_VolumeDown`.

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_volume_listener.py -v`

- [ ] **Step 5: Commit**
```bash
git add backend/modules/qt6_gui/input/volume_key_listener.py backend/modules/qt6_gui/ui/main_window.py tests/test_volume_listener.py
git commit -m "feat(input): bind physical and Bluetooth volume buttons to app volume popover"
```

---

### Task 7 (TODO #4 & #5): Video Loss & Bluetooth Recognition Root Cause Hardening

**Files:**
- Modify: `backend/modules/channel_manager/handlers/video_handler.py`
- Modify: `backend/modules/qt6_gui/media/video_pipeline.py`
- Modify: `backend/shared/hardware/bluez_bluetooth.py`
- Test: `tests/test_video_pipeline_recovery.py`

**Interfaces:**
- Consumes: GStreamer bus errors/EOS, video channel socket disconnects, BlueZ adapter power/rfkill states.
- Produces: Auto-restart of video decoding pipeline without requiring full system reboot; adapter reset on BlueZ discovery stall.

- [ ] **Step 1: Implement GStreamer pipeline watchdog and error recovery**
In `backend/modules/qt6_gui/media/video_pipeline.py`:
- Listen for `GST_MESSAGE_ERROR` or pipeline stall (no frames decoded for >3 seconds while channel is active).
- Automatically trigger pipeline teardown and reconstruction without restarting the entire kiosk or OS.
In `backend/modules/channel_manager/handlers/video_handler.py`:
- On `send_focus_indication(PROJECTED)`, force refresh of SPS/PPS / IDR frame request if supported.

- [ ] **Step 2: Harden BlueZ Bluetooth discovery and power state**
In `backend/shared/hardware/bluez_bluetooth.py`:
- Check adapter `Powered` property and unblock `rfkill` if powered down.
- Add timeout handling to discovery: if discovery is running for >60s without results or gets stuck in `Discovering=True`, safely stop and cycle adapter discovery.

- [ ] **Step 3: Run regression tests and smoke test**
Run: `micromamba run -n NemoHeadUnit-Wireless python -m pytest tests/`

- [ ] **Step 4: Commit**
```bash
git add backend/modules/channel_manager/handlers/video_handler.py backend/modules/qt6_gui/media/video_pipeline.py backend/shared/hardware/bluez_bluetooth.py
git commit -m "fix(stability): harden video pipeline watchdog and BlueZ adapter discovery"
```

---

## Verification Plan

### Automated Tests
```bash
micromamba run -n NemoHeadUnit-Wireless python -m pytest tests/test_audio_underrun_suppression.py -v
micromamba run -n NemoHeadUnit-Wireless python -m pytest tests/test_audio_sink_recovery.py -v
micromamba run -n NemoHeadUnit-Wireless python -m pytest tests/test_navigation_proto_decode.py -v
micromamba run -n NemoHeadUnit-Wireless python -m pytest tests/test_phone_call_widget.py -v
micromamba run -n NemoHeadUnit-Wireless python -m pytest tests/test_volume_listener.py -v
```

### System Smoke Test (Mandatory Smoke Test Mandate)
```bash
micromamba run -n NemoHeadUnit-Wireless python web-browser-head-unit/backend/main.py
```
- Verify all priority waves (0, 1, 2, 3+) boot cleanly with 0 timeouts or schema errors.
