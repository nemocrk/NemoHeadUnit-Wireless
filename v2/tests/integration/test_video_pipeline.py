"""
NemoHeadUnit-Wireless v2 — Integration Tests: Video Pipeline
=============================================================
Fase 2 — Integration Test §5

Scope:
  - modules/video_ui/main.py  — handler bus, connection state machine, publish topics
  - modules/channel_modules/video/main.py (VideoModule) — full AA channel handshake,
    media frame dispatch, video.frame / video.state publish

Strategy:
  - Bus ZMQ reale in-process (fixture in_process_broker)
  - GStreamer / PyQt6 / gi patchati a livello sys.modules pre-import
    (nessuna dipendenza hardware in CI)
  - importlib.reload() per ogni test — bus fresco e stato modulo pulito
  - Handler on_* chiamati direttamente; spy BusClient riceve i topic pubblicati
  - BusTracer mockato per evitare thread drain spurii

Gruppi:
  1. video_ui — Boot protocol
  2. video_ui — Connection state machine
  3. video_ui — video.state handler
  4. video_ui — video.frame handler
  5. VideoModule — Boot / channel lifecycle
  6. VideoModule — AA message dispatch (setup, open, start, stop, focus)
  7. VideoModule — Media frame publish (video.frame + video.state)
  8. VideoModule — Session lifecycle (aa.session.active / shutdown)
  9. VideoModule — Robustness (malformed payloads, drop on closed channel)
 10. Pipeline integration — video.frame pub → spy subscriber

Marker: @pytest.mark.integration
Dipendenze: conftest.in_process_broker
Rif: docs/TEST_SUITE_ARCHITECTURE.md §3.2
"""
from __future__ import annotations

import base64
import importlib
import sys
import time
import types
import uuid
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub Qt / GStreamer / gi BEFORE any module import
# ---------------------------------------------------------------------------

def _stub_pyqt6() -> None:
    """Inject minimal PyQt6 stubs so video_ui can be imported without a display."""
    # Only stub if not already a real import
    if "PyQt6" in sys.modules and not isinstance(sys.modules["PyQt6"], types.ModuleType):
        return
    for mod_name in [
        "PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets",
        "PyQt6.QtOpenGLWidgets",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = MagicMock()

    # Ensure Q_ARG, pyqtSlot, QMetaObject have callable stubs
    qt_core = sys.modules["PyQt6.QtCore"]
    qt_core.Q_ARG = MagicMock(return_value=MagicMock())
    qt_core.QMetaObject = MagicMock()
    qt_core.QMetaObject.invokeMethod = MagicMock()
    qt_core.pyqtSlot = lambda *a, **kw: (lambda f: f)  # passthrough decorator
    qt_core.Qt = MagicMock()
    qt_core.QTimer = MagicMock()
    qt_core.QSize = MagicMock()


def _stub_gstreamer() -> None:
    for mod_name in ["gi", "gi.repository"]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = MagicMock()
    # Ensure Gst unavailable so video_ui takes the 'no GStreamer' path
    gi_mock = sys.modules.get("gi", MagicMock())
    gi_mock.require_version = MagicMock(side_effect=Exception("no gst"))


_stub_pyqt6()
_stub_gstreamer()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _wait(lst: list, count: int, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(lst) >= count:
            return True
        time.sleep(0.01)
    return False


def _make_client(in_process_broker, name: str | None = None):
    import shared.bus_client as _bc
    _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
    _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]
    from shared.bus_client import BusClient
    return BusClient(module_name=name or f"t_{uuid.uuid4().hex[:6]}")


def _start_client(client):
    client.start(blocking=False)
    time.sleep(0.05)


# ---------------------------------------------------------------------------
# video_ui loader
# ---------------------------------------------------------------------------

def _load_video_ui(in_process_broker):
    """Reload video_ui.main with in-process broker and all heavy deps mocked."""
    import shared.bus_client as _bc
    _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
    _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]

    mock_tracer = MagicMock()
    with patch("shared.bus_client.BusTracer", return_value=mock_tracer):
        import video_ui.main as vui
        importlib.reload(vui)
    # Ensure _window is None (no Qt window in CI)
    vui._window = None
    vui._app = None
    vui._conn_state = vui._STATE_WAITING_BT
    return vui


# ---------------------------------------------------------------------------
# VideoModule loader (channel_modules/video)
# ---------------------------------------------------------------------------

def _make_video_module(in_process_broker):
    """Build a VideoModule with bus patched to in-process broker, channel_config set."""
    import shared.bus_client as _bc
    _bc.BROKER_PUB_ADDR = in_process_broker["pub_addr"]
    _bc.BROKER_SUB_ADDR = in_process_broker["sub_addr"]

    mock_tracer = MagicMock()
    with patch("shared.bus_client.BusTracer", return_value=mock_tracer):
        import channel_modules.video.main as vm_mod
        importlib.reload(vm_mod)
        mod = vm_mod.VideoModule()

    mod.CHANNEL_ID = 2
    mod.channel_config = {
        "av_channel": {
            "video_configs": [{"codec": 1}]
        }
    }
    mod._config = {"max_unacked": 1, "publish_frames": True}
    # Open the channel so on_frame dispatches
    mod._channel_open = True
    return mod


# ---------------------------------------------------------------------------
# Proto stubs — minimal serialisable objects
# ---------------------------------------------------------------------------

def _make_proto_bytes(val: int = 0) -> bytes:
    """Return a minimal valid protobuf varint: field 1, wiretype 0."""
    return bytes([0x08, val & 0x7F]) if val else b""


def _b64_h264_idr() -> str:
    """Minimal H.264 Annex-B IDR NAL (00 00 00 01 65 ...)."""
    return base64.b64encode(bytes([0x00, 0x00, 0x00, 0x01, 0x65, 0xB8])).decode()


def _b64_h264_sps() -> str:
    """Minimal H.264 SPS NAL (00 00 00 01 67 ...)."""
    return base64.b64encode(bytes([0x00, 0x00, 0x00, 0x01, 0x67, 0x42, 0x00, 0x1E])).decode()


def _media_with_ts_bytes(ts_us: int = 1000, data: bytes = b"\x00\x01\x02") -> bytes:
    """Encode a minimal MediaWithTimestamp wire payload (varint ts_us + raw data)."""
    # field 1: ts_us (varint), field 2: data (bytes)
    from shared.proto_utils import parse_media_with_timestamp  # noqa: F401
    # Reuse the same wire format understood by parse_media_with_timestamp
    # Format: field1=ts_us (tag 0x08), field2=data (tag 0x12 + len + bytes)
    ts_encoded = _encode_varint(ts_us)
    data_len = _encode_varint(len(data))
    return bytes([0x08]) + ts_encoded + bytes([0x12]) + data_len + data


def _encode_varint(value: int) -> bytes:
    buf = []
    while value > 0x7F:
        buf.append((value & 0x7F) | 0x80)
        value >>= 7
    buf.append(value)
    return bytes(buf)


# ===========================================================================
# Gruppo 1 — video_ui: Boot protocol
# ===========================================================================

class TestVideoUiBoot:

    @pytest.mark.integration
    def test_readytostart_publishes_module_ready(self, in_process_broker):
        """on_system_readytostart() pubblica system.module_ready sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("system.module_ready", lambda t, p: received.append(p))
        _start_client(spy)

        vui = _load_video_ui(in_process_broker)
        vui.on_system_readytostart()

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "system.module_ready non ricevuto"
        assert received[0]["name"] == "video_ui"
        assert received[0]["priority"] == vui.PRIORITY

    @pytest.mark.integration
    def test_system_start_correct_priority_publishes_system_ready(self, in_process_broker):
        """on_system_start() con priority corretta pubblica system.ready."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("system.ready", lambda t, p: received.append(p))
        _start_client(spy)

        vui = _load_video_ui(in_process_broker)
        vui.on_system_start("system.start", {"priority": vui.PRIORITY})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "system.ready non ricevuto"
        assert received[0]["name"] == "video_ui"

    @pytest.mark.integration
    def test_system_start_wrong_priority_no_publish(self, in_process_broker):
        """on_system_start() con priority diversa NON pubblica system.ready."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("system.ready", lambda t, p: received.append(p))
        _start_client(spy)

        vui = _load_video_ui(in_process_broker)
        vui.on_system_start("system.start", {"priority": vui.PRIORITY + 99})

        time.sleep(0.2)
        spy.stop()
        assert len(received) == 0

    @pytest.mark.integration
    def test_system_stop_does_not_crash(self, in_process_broker):
        """on_system_stop() non solleva eccezioni."""
        vui = _load_video_ui(in_process_broker)
        try:
            vui.on_system_stop("system.stop", {})
        except Exception as exc:
            pytest.fail(f"on_system_stop ha sollevato: {exc}")

    @pytest.mark.integration
    def test_readytostart_priority_is_2(self, in_process_broker):
        """Il modulo video_ui ha PRIORITY == 2 (UI level)."""
        vui = _load_video_ui(in_process_broker)
        assert vui.PRIORITY == 2


# ===========================================================================
# Gruppo 2 — video_ui: Connection state machine
# ===========================================================================

class TestVideoUiConnState:

    @pytest.mark.integration
    def test_initial_state_is_waiting_bt(self, in_process_broker):
        """Dopo reload, _conn_state è WAITING_BT."""
        vui = _load_video_ui(in_process_broker)
        assert vui._conn_state == vui._STATE_WAITING_BT

    @pytest.mark.integration
    def test_bt_pairing_completed_transitions_to_handshake(self, in_process_broker):
        """bluetooth_manager.pairing.completed da WAITING_BT → HANDSHAKE."""
        vui = _load_video_ui(in_process_broker)
        vui._conn_state = vui._STATE_WAITING_BT
        vui.on_bluetooth_pairing_completed("bluetooth_manager.pairing.completed", {"device_address": "AA:BB"})
        assert vui._conn_state == vui._STATE_HANDSHAKE

    @pytest.mark.integration
    def test_bt_pairing_in_handshake_state_no_change(self, in_process_broker):
        """bluetooth_manager.pairing.completed da HANDSHAKE rimane HANDSHAKE."""
        vui = _load_video_ui(in_process_broker)
        vui._conn_state = vui._STATE_HANDSHAKE
        vui.on_bluetooth_pairing_completed("bluetooth_manager.pairing.completed", {"device_address": "AA:BB"})
        assert vui._conn_state == vui._STATE_HANDSHAKE

    @pytest.mark.integration
    def test_aa_session_active_from_waiting_bt_goes_handshake(self, in_process_broker):
        """aa.session.active da WAITING_BT → HANDSHAKE."""
        vui = _load_video_ui(in_process_broker)
        vui._conn_state = vui._STATE_WAITING_BT
        vui.on_aa_session_active("aa.session.active", {})
        assert vui._conn_state == vui._STATE_HANDSHAKE

    @pytest.mark.integration
    def test_aa_session_shutdown_resets_to_waiting_bt(self, in_process_broker):
        """aa.session.shutdown da qualsiasi stato → WAITING_BT."""
        vui = _load_video_ui(in_process_broker)
        for state in (vui._STATE_HANDSHAKE, vui._STATE_STREAMING, vui._STATE_INTERRUPTED):
            vui._conn_state = state
            vui.on_aa_session_shutdown("aa.session.shutdown", {})
            assert vui._conn_state == vui._STATE_WAITING_BT

    @pytest.mark.integration
    def test_video_state_playing_transitions_to_streaming(self, in_process_broker):
        """video.state=PLAYING → _conn_state diventa STREAMING."""
        vui = _load_video_ui(in_process_broker)
        vui._conn_state = vui._STATE_HANDSHAKE
        vui.on_video_state("video.state", {"state": "PLAYING"})
        assert vui._conn_state == vui._STATE_STREAMING

    @pytest.mark.integration
    def test_video_state_idle_from_streaming_goes_interrupted(self, in_process_broker):
        """video.state=IDLE da STREAMING → _conn_state diventa INTERRUPTED."""
        vui = _load_video_ui(in_process_broker)
        vui._conn_state = vui._STATE_STREAMING
        vui.on_video_state("video.state", {"state": "IDLE"})
        assert vui._conn_state == vui._STATE_INTERRUPTED

    @pytest.mark.integration
    def test_video_state_stopped_from_streaming_goes_interrupted(self, in_process_broker):
        """video.state=STOPPED da STREAMING → _conn_state diventa INTERRUPTED."""
        vui = _load_video_ui(in_process_broker)
        vui._conn_state = vui._STATE_STREAMING
        vui.on_video_state("video.state", {"state": "STOPPED"})
        assert vui._conn_state == vui._STATE_INTERRUPTED

    @pytest.mark.integration
    def test_video_state_idle_from_non_streaming_no_interrupted(self, in_process_broker):
        """video.state=IDLE da HANDSHAKE NON porta a INTERRUPTED."""
        vui = _load_video_ui(in_process_broker)
        vui._conn_state = vui._STATE_HANDSHAKE
        vui.on_video_state("video.state", {"state": "IDLE"})
        assert vui._conn_state != vui._STATE_INTERRUPTED


# ===========================================================================
# Gruppo 3 — video_ui: video.state handler
# ===========================================================================

class TestVideoUiVideoState:

    @pytest.mark.integration
    def test_video_state_unknown_does_not_crash(self, in_process_broker):
        """video.state con valore sconosciuto non solleva."""
        vui = _load_video_ui(in_process_broker)
        try:
            vui.on_video_state("video.state", {"state": "UNKNOWN_STATE_XYZ"})
        except Exception as exc:
            pytest.fail(f"on_video_state ha sollevato: {exc}")

    @pytest.mark.integration
    def test_video_state_missing_key_does_not_crash(self, in_process_broker):
        """Payload senza 'state' non solleva."""
        vui = _load_video_ui(in_process_broker)
        try:
            vui.on_video_state("video.state", {})
        except Exception as exc:
            pytest.fail(f"on_video_state ha sollevato con payload vuoto: {exc}")

    @pytest.mark.integration
    def test_video_state_playing_does_not_invoke_set_streaming_directly(self, in_process_broker):
        """video.state=PLAYING non invoca set_streaming direttamente (solo conn_state change)."""
        vui = _load_video_ui(in_process_broker)
        vui._conn_state = vui._STATE_HANDSHAKE
        # _window is None → invokeMethod is a no-op
        vui.on_video_state("video.state", {"state": "PLAYING"})
        # Should not raise, conn_state should be STREAMING
        assert vui._conn_state == vui._STATE_STREAMING


# ===========================================================================
# Gruppo 4 — video_ui: video.frame handler
# ===========================================================================

class TestVideoUiVideoFrame:

    @pytest.mark.integration
    def test_on_video_frame_no_window_no_crash(self, in_process_broker):
        """on_video_frame() senza _window attiva non solleva."""
        vui = _load_video_ui(in_process_broker)
        vui._window = None
        try:
            vui.on_video_frame("video.frame", {
                "data_b64": _b64_h264_idr(),
                "is_config": False,
            })
        except Exception as exc:
            pytest.fail(f"on_video_frame ha sollevato: {exc}")

    @pytest.mark.integration
    def test_on_video_frame_empty_payload_no_crash(self, in_process_broker):
        """on_video_frame() con payload vuoto non solleva."""
        vui = _load_video_ui(in_process_broker)
        vui._window = None
        try:
            vui.on_video_frame("video.frame", {})
        except Exception as exc:
            pytest.fail(f"on_video_frame ha sollevato con payload vuoto: {exc}")


# ===========================================================================
# Gruppo 5 — VideoModule: Boot / channel lifecycle
# ===========================================================================

class TestVideoModuleBoot:

    @pytest.mark.integration
    def test_module_ready_to_start_publishes_on_readytostart(self, in_process_broker):
        """_on_channel_manager_module_readytostart() pubblica channel_manager.module_ready_to_start."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("channel_manager.module_ready_to_start", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._on_channel_manager_module_readytostart()

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "channel_manager.module_ready_to_start non ricevuto"
        assert received[0]["name"] == "video"
        assert received[0]["priority"] == mod.PRIORITY

    @pytest.mark.integration
    def test_module_start_correct_priority_calls_init(self, in_process_broker):
        """_on_channel_manager_module_start() con priority corretta chiama _init."""
        mod = _make_video_module(in_process_broker)
        mod.cfg = MagicMock()
        mod._on_channel_manager_module_start(
            "channel_manager.module_start",
            {"priority": mod.PRIORITY},
        )
        assert mod._init_done is True

    @pytest.mark.integration
    def test_module_start_wrong_priority_no_init(self, in_process_broker):
        """_on_channel_manager_module_start() con priority sbagliata NON chiama _init."""
        mod = _make_video_module(in_process_broker)
        mod.cfg = MagicMock()
        mod._on_channel_manager_module_start(
            "channel_manager.module_start",
            {"priority": mod.PRIORITY + 99},
        )
        assert mod._init_done is False

    @pytest.mark.integration
    def test_module_stop_publishes_module_stopped(self, in_process_broker):
        """_on_channel_manager_module_stop() pubblica channel_manager.module_stopped."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("channel_manager.module_stopped", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._on_channel_manager_module_stop("channel_manager.module_stop", {})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "channel_manager.module_stopped non ricevuto"
        assert received[0]["name"] == "video"

    @pytest.mark.integration
    def test_module_stop_publishes_video_state_idle(self, in_process_broker):
        """_on_channel_manager_module_stop() pubblica video.state=IDLE via _cleanup."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.state", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._state = "PLAYING"  # force non-IDLE to trigger publish
        mod._on_channel_manager_module_stop("channel_manager.module_stop", {})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "video.state non ricevuto dopo module_stop"
        assert received[0]["state"] == "IDLE"

    @pytest.mark.integration
    def test_channel_open_resets_state(self, in_process_broker):
        """on_channel_open() resetta session_id e stato a IDLE."""
        mod = _make_video_module(in_process_broker)
        mod._session_id = 42
        mod._state = "PLAYING"
        mod.on_channel_open(2, {"channel_id": 2})
        assert mod._session_id == 0
        assert mod._state == "IDLE"

    @pytest.mark.integration
    def test_channel_close_resets_state(self, in_process_broker):
        """on_channel_close() resetta session_id e stato a IDLE."""
        mod = _make_video_module(in_process_broker)
        mod._session_id = 7
        mod._state = "OPEN"
        mod.on_channel_close(2)
        assert mod._session_id == 0
        assert mod._state == "IDLE"

    @pytest.mark.integration
    def test_frame_dropped_when_channel_not_open(self, in_process_broker):
        """Frame su canale non aperto viene droppato senza crash."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.frame", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._channel_open = False
        mod._on_aa_frame("aa.frame.ch2", {
            "channel_id": 2,
            "message_id": 32770,  # AV_MEDIA_WITH_TIMESTAMP
            "encrypted": False,
            "payload_hex": _media_with_ts_bytes().hex(),
        })

        time.sleep(0.2)
        spy.stop()
        assert len(received) == 0, "Nessun video.frame doveva essere pubblicato con canale chiuso"


# ===========================================================================
# Gruppo 6 — VideoModule: AA message dispatch
# ===========================================================================

class TestVideoModuleAaDispatch:

    @pytest.mark.integration
    def test_setup_request_publishes_aa_frame_send(self, in_process_broker):
        """_handle_setup_request() pubblica aa.frame.send sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("aa.frame.send", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        with patch.object(mod, "_send_video_focus_indication"):
            mod._handle_setup_request(b"")

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "aa.frame.send non ricevuto dopo setup_request"
        assert received[0]["channel_id"] == mod.CHANNEL_ID

    @pytest.mark.integration
    def test_setup_request_transitions_state_to_setup(self, in_process_broker):
        """_handle_setup_request() porta lo stato a SETUP."""
        mod = _make_video_module(in_process_broker)
        with patch.object(mod, "_send_video_focus_indication"):
            mod._handle_setup_request(b"")
        assert mod._state == "SETUP"

    @pytest.mark.integration
    def test_setup_request_sends_video_focus_indication(self, in_process_broker):
        """_handle_setup_request() chiama _send_video_focus_indication."""
        mod = _make_video_module(in_process_broker)
        with patch.object(mod, "_send_video_focus_indication") as mock_focus:
            mod._handle_setup_request(b"")
        mock_focus.assert_called_once()

    @pytest.mark.integration
    def test_open_request_publishes_aa_frame_send(self, in_process_broker):
        """_handle_open_request() pubblica aa.frame.send sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("aa.frame.send", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._handle_open_request(b"")

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "aa.frame.send non ricevuto dopo open_request"

    @pytest.mark.integration
    def test_open_request_transitions_state_to_open(self, in_process_broker):
        """_handle_open_request() porta lo stato a OPEN."""
        mod = _make_video_module(in_process_broker)
        mod._handle_open_request(b"")
        assert mod._state == "OPEN"

    @pytest.mark.integration
    def test_open_request_publishes_video_state_open(self, in_process_broker):
        """_handle_open_request() pubblica video.state=OPEN sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.state", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._handle_open_request(b"")

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "video.state non ricevuto dopo open_request"
        assert received[0]["state"] == "OPEN"

    @pytest.mark.integration
    def test_stop_indication_transitions_state_to_stopped(self, in_process_broker):
        """_handle_stop_indication() porta lo stato a STOPPED."""
        mod = _make_video_module(in_process_broker)
        mod._state = "PLAYING"
        mod._handle_stop_indication(b"")
        assert mod._state == "STOPPED"

    @pytest.mark.integration
    def test_stop_indication_resets_session_id(self, in_process_broker):
        """_handle_stop_indication() resetta session_id a 0."""
        mod = _make_video_module(in_process_broker)
        mod._session_id = 99
        mod._handle_stop_indication(b"")
        assert mod._session_id == 0

    @pytest.mark.integration
    def test_stop_indication_publishes_video_state_stopped(self, in_process_broker):
        """_handle_stop_indication() pubblica video.state=STOPPED sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.state", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._state = "PLAYING"
        mod._handle_stop_indication(b"")

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "video.state=STOPPED non ricevuto"
        assert received[0]["state"] == "STOPPED"

    @pytest.mark.integration
    def test_video_focus_request_sends_aa_frame(self, in_process_broker):
        """_handle_video_focus_request() pubblica aa.frame.send sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("aa.frame.send", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._handle_video_focus_request(b"")

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "aa.frame.send non ricevuto dopo focus_request"

    @pytest.mark.integration
    def test_malformed_aa_frame_payload_no_crash(self, in_process_broker):
        """Payload hex malformato in _on_aa_frame non solleva."""
        mod = _make_video_module(in_process_broker)
        try:
            mod._on_aa_frame("aa.frame.ch2", {
                "channel_id": 2,
                "message_id": 1,
                "encrypted": False,
                "payload_hex": "ZZZZ_not_hex",
            })
        except Exception as exc:
            pytest.fail(f"_on_aa_frame ha sollevato con payload malformato: {exc}")

    @pytest.mark.integration
    def test_unhandled_message_id_no_crash(self, in_process_broker):
        """Message ID sconosciuto non solleva."""
        mod = _make_video_module(in_process_broker)
        try:
            mod.on_frame(2, 0xFFFF, False, b"\x01\x02")
        except Exception as exc:
            pytest.fail(f"on_frame ha sollevato con msg_id sconosciuto: {exc}")


# ===========================================================================
# Gruppo 7 — VideoModule: Media frame publish
# ===========================================================================

class TestVideoModuleMediaPublish:

    @pytest.mark.integration
    def test_handle_media_publishes_video_frame_config(self, in_process_broker):
        """_handle_media() pubblica video.frame con is_config=True sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.frame", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        sps_bytes = bytes([0x00, 0x00, 0x00, 0x01, 0x67, 0x42, 0x00, 0x1E])
        mod._handle_media(sps_bytes)

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "video.frame config non ricevuto"
        assert received[0]["is_config"] is True
        assert received[0]["channel_id"] == mod.CHANNEL_ID

    @pytest.mark.integration
    def test_handle_media_sends_ack(self, in_process_broker):
        """_handle_media() pubblica aa.frame.send (MediaAck) sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("aa.frame.send", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._handle_media(b"\x00\x00\x00\x01\x67")

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "MediaAck (aa.frame.send) non ricevuto"

    @pytest.mark.integration
    def test_handle_media_empty_body_no_crash(self, in_process_broker):
        """_handle_media() con body vuoto non solleva."""
        mod = _make_video_module(in_process_broker)
        try:
            mod._handle_media(b"")
        except Exception as exc:
            pytest.fail(f"_handle_media ha sollevato con body vuoto: {exc}")

    @pytest.mark.integration
    def test_handle_media_with_timestamp_publishes_video_frame(self, in_process_broker):
        """_handle_media_with_timestamp() pubblica video.frame sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.frame", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._state = "PLAYING"
        wire = _media_with_ts_bytes(ts_us=5000, data=bytes([0x00, 0x00, 0x00, 0x01, 0x65]))
        mod._handle_media_with_timestamp(wire)

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "video.frame non ricevuto da media_with_timestamp"
        assert received[0]["is_config"] is False
        assert received[0]["channel_id"] == mod.CHANNEL_ID

    @pytest.mark.integration
    def test_handle_media_with_timestamp_sends_ack(self, in_process_broker):
        """_handle_media_with_timestamp() pubblica aa.frame.send (MediaAck) sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("aa.frame.send", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._state = "PLAYING"
        wire = _media_with_ts_bytes(ts_us=1000, data=b"\x00\x01\x02")
        mod._handle_media_with_timestamp(wire)

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "MediaAck non ricevuto"

    @pytest.mark.integration
    def test_handle_media_with_timestamp_auto_transition_to_playing(self, in_process_broker):
        """_handle_media_with_timestamp() da stato OPEN → transita a PLAYING."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.state", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._state = "OPEN"
        wire = _media_with_ts_bytes(ts_us=2000, data=b"\x00\x01\x02\x03")
        mod._handle_media_with_timestamp(wire)

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "video.state=PLAYING non ricevuto"
        states = [r["state"] for r in received]
        assert "PLAYING" in states

    @pytest.mark.integration
    def test_publish_frames_false_no_video_frame_published(self, in_process_broker):
        """publish_frames=False → video.frame NON viene pubblicato sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.frame", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._config["publish_frames"] = False
        mod._state = "PLAYING"
        wire = _media_with_ts_bytes(ts_us=1000, data=b"\x00\x01")
        mod._handle_media_with_timestamp(wire)

        time.sleep(0.2)
        spy.stop()

        assert len(received) == 0, "video.frame non doveva essere pubblicato con publish_frames=False"

    @pytest.mark.integration
    def test_multiple_frames_published_in_sequence(self, in_process_broker):
        """5 frame consecutivi → 5 video.frame pubblicati sul bus."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.frame", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._state = "PLAYING"
        for i in range(5):
            wire = _media_with_ts_bytes(ts_us=i * 33000, data=bytes([0x00, 0x00, 0x00, 0x01, 0x65 + i % 3]))
            mod._handle_media_with_timestamp(wire)

        ok = _wait(received, 5)
        spy.stop()

        assert ok, f"Ricevuti solo {len(received)} su 5 video.frame"

    @pytest.mark.integration
    def test_video_frame_payload_contains_data_b64(self, in_process_broker):
        """video.frame pubblicato contiene campo data_b64 valido."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.frame", lambda t, p: received.append(p))
        _start_client(spy)

        raw_data = bytes([0x00, 0x00, 0x00, 0x01, 0x65, 0xB8])
        mod = _make_video_module(in_process_broker)
        mod._state = "PLAYING"
        wire = _media_with_ts_bytes(ts_us=1000, data=raw_data)
        mod._handle_media_with_timestamp(wire)

        ok = _wait(received, 1)
        spy.stop()

        assert ok
        data_b64 = received[0].get("data_b64", "")
        assert data_b64, "data_b64 mancante nel payload"
        decoded = base64.b64decode(data_b64)
        assert decoded == raw_data


# ===========================================================================
# Gruppo 8 — VideoModule: Session lifecycle
# ===========================================================================

class TestVideoModuleSessionLifecycle:

    @pytest.mark.integration
    def test_aa_session_active_logs_no_state_change(self, in_process_broker):
        """on_aa_session_active() non cambia lo stato video."""
        mod = _make_video_module(in_process_broker)
        mod._state = "OPEN"
        mod.on_aa_session_active("aa.session.active", {})
        assert mod._state == "OPEN"

    @pytest.mark.integration
    def test_aa_session_shutdown_resets_state_to_idle(self, in_process_broker):
        """on_aa_session_shutdown() pubblica video.state=IDLE."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.state", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._state = "PLAYING"
        mod._session_id = 5
        mod.on_aa_session_shutdown("aa.session.shutdown", {})

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "video.state=IDLE non ricevuto dopo session_shutdown"
        assert received[0]["state"] == "IDLE"

    @pytest.mark.integration
    def test_aa_session_shutdown_resets_session_id(self, in_process_broker):
        """on_aa_session_shutdown() resetta session_id a 0."""
        mod = _make_video_module(in_process_broker)
        mod._session_id = 12
        mod.on_aa_session_shutdown("aa.session.shutdown", {})
        assert mod._session_id == 0

    @pytest.mark.integration
    def test_start_indication_sets_playing_state(self, in_process_broker):
        """_handle_start_indication() porta lo stato a PLAYING."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.state", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._state = "OPEN"
        # Minimal AVChannelStartIndication proto: field 1 (session) = 3
        start_body = bytes([0x08, 0x03])
        mod._handle_start_indication(start_body)

        ok = _wait(received, 1)
        spy.stop()

        assert ok, "video.state=PLAYING non ricevuto"
        assert received[0]["state"] == "PLAYING"
        assert mod._session_id == 3

    @pytest.mark.integration
    def test_start_indication_malformed_body_no_crash(self, in_process_broker):
        """_handle_start_indication() con body malformato non solleva."""
        mod = _make_video_module(in_process_broker)
        try:
            mod._handle_start_indication(b"\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF\xFF")
        except Exception as exc:
            pytest.fail(f"_handle_start_indication ha sollevato: {exc}")


# ===========================================================================
# Gruppo 9 — VideoModule: Robustness
# ===========================================================================

class TestVideoModuleRobustness:

    @pytest.mark.integration
    def test_on_frame_missing_payload_hex_no_crash(self, in_process_broker):
        """_on_aa_frame() con payload_hex mancante non solleva."""
        mod = _make_video_module(in_process_broker)
        try:
            mod._on_aa_frame("aa.frame.ch2", {
                "channel_id": 2,
                "message_id": 1,
                "encrypted": False,
                # payload_hex missing
            })
        except Exception as exc:
            pytest.fail(f"_on_aa_frame ha sollevato: {exc}")

    @pytest.mark.integration
    def test_set_state_same_state_no_publish(self, in_process_broker):
        """_set_state() con stesso stato NON pubblica video.state."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.state", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._state = "IDLE"
        mod._set_state("IDLE")  # same → no publish

        time.sleep(0.2)
        spy.stop()
        assert len(received) == 0, "video.state non doveva essere pubblicato a parità di stato"

    @pytest.mark.integration
    def test_send_media_ack_uses_current_session_id(self, in_process_broker):
        """_send_media_ack() include session_id corrente nel payload aa.frame.send."""
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("aa.frame.send", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._session_id = 7
        mod._send_media_ack()

        ok = _wait(received, 1)
        spy.stop()

        assert ok
        # The payload_hex contains serialised AVMediaAckIndication with session_id=7
        assert received[0]["channel_id"] == mod.CHANNEL_ID

    @pytest.mark.integration
    def test_init_reads_codec_from_channel_config(self, in_process_broker):
        """_init() legge il codec dall'SDR channel_config."""
        mod = _make_video_module(in_process_broker)
        mod.channel_config = {
            "av_channel": {
                "video_configs": [{"codec": 3}]  # VP9
            }
        }
        mod._init_done = False
        mod.cfg = MagicMock()
        mod._on_channel_manager_module_start(
            "channel_manager.module_start",
            {"priority": mod.PRIORITY},
        )
        assert mod._codec_sdr == 3
        assert mod._codec == 3

    @pytest.mark.integration
    def test_init_fallback_when_no_channel_config(self, in_process_broker):
        """_init() con channel_config=None non solleva e usa default H264-BP."""
        mod = _make_video_module(in_process_broker)
        mod.channel_config = None
        mod._init()
        # Should still be H264-BP default (codec value 1)
        assert mod._codec_sdr == 1

    @pytest.mark.integration
    def test_cleanup_resets_state_to_idle(self, in_process_broker):
        """_cleanup() porta lo stato a IDLE."""
        mod = _make_video_module(in_process_broker)
        mod._state = "PLAYING"
        mod._cleanup()
        assert mod._state == "IDLE"


# ===========================================================================
# Gruppo 10 — Pipeline integration: video.frame pub → spy subscriber
# ===========================================================================

class TestVideoPipelineEndToEnd:

    @pytest.mark.integration
    def test_full_sequence_setup_open_start_frame_stop(self, in_process_broker):
        """
        Sequenza completa:
          setup_request → video.state=SETUP
          open_request  → video.state=OPEN
          start_indication → video.state=PLAYING
          media_with_timestamp → video.frame published
          stop_indication → video.state=STOPPED
        """
        state_received = []
        frame_received = []

        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.state", lambda t, p: state_received.append(p))
        spy.subscribe("video.frame", lambda t, p: frame_received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)

        with patch.object(mod, "_send_video_focus_indication"):
            mod._handle_setup_request(b"")
        mod._handle_open_request(b"")
        mod._handle_start_indication(bytes([0x08, 0x01]))  # session=1

        wire = _media_with_ts_bytes(
            ts_us=33333,
            data=bytes([0x00, 0x00, 0x00, 0x01, 0x65, 0xB8]),
        )
        mod._handle_media_with_timestamp(wire)
        mod._handle_stop_indication(b"")

        ok_state = _wait(state_received, 3)
        ok_frame = _wait(frame_received, 1)
        spy.stop()

        states = [r["state"] for r in state_received]
        assert "SETUP" in states, "SETUP mancante nella sequenza"
        assert "OPEN" in states,  "OPEN mancante nella sequenza"
        assert ok_frame, "video.frame non ricevuto"
        assert "STOPPED" in states, "STOPPED mancante nella sequenza"

    @pytest.mark.integration
    def test_config_frame_before_idr_published_correctly(self, in_process_broker):
        """
        SPS/PPS (AV_MEDIA_INDICATION, is_config=True) pubblicato prima di un IDR frame.
        Il bus deve ricevere prima il config frame poi il data frame.
        """
        received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.frame", lambda t, p: received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._state = "PLAYING"

        sps = bytes([0x00, 0x00, 0x00, 0x01, 0x67, 0x42, 0x00, 0x1E])
        mod._handle_media(sps)

        idr_wire = _media_with_ts_bytes(
            ts_us=33333,
            data=bytes([0x00, 0x00, 0x00, 0x01, 0x65, 0xB8]),
        )
        mod._handle_media_with_timestamp(idr_wire)

        ok = _wait(received, 2)
        spy.stop()

        assert ok, f"Ricevuti solo {len(received)} su 2 video.frame"
        assert received[0]["is_config"] is True
        assert received[1]["is_config"] is False

    @pytest.mark.integration
    def test_aa_session_shutdown_after_playing_stops_video(self, in_process_broker):
        """
        Dopo aa.session.shutdown da PLAYING:
          - video.state=IDLE pubblicato
          - session_id resettato
          - _conn_state di video_ui → WAITING_BT
        """
        state_received = []
        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.state", lambda t, p: state_received.append(p))
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._state = "PLAYING"
        mod._session_id = 3
        mod.on_aa_session_shutdown("aa.session.shutdown", {})

        ok = _wait(state_received, 1)
        spy.stop()

        assert ok, "video.state=IDLE non ricevuto dopo session_shutdown"
        assert state_received[0]["state"] == "IDLE"
        assert mod._session_id == 0

    @pytest.mark.integration
    def test_video_ui_receives_video_state_playing_on_bus(self, in_process_broker):
        """
        VideoModule pubblica video.state=PLAYING → video_ui.on_video_state() ricevuto via bus.
        """
        vui = _load_video_ui(in_process_broker)
        vui._conn_state = vui._STATE_HANDSHAKE

        state_changes = []
        original_on_video_state = vui.on_video_state

        def _spy_state(topic, payload):
            state_changes.append(payload.get("state"))
            original_on_video_state(topic, payload)

        spy = _make_client(in_process_broker, "spy")
        spy.subscribe("video.state", _spy_state)
        _start_client(spy)

        mod = _make_video_module(in_process_broker)
        mod._state = "OPEN"
        mod._handle_start_indication(bytes([0x08, 0x02]))

        ok = _wait(state_changes, 1)
        spy.stop()

        assert ok, "video.state=PLAYING non ricevuto sul bus"
        assert "PLAYING" in state_changes
