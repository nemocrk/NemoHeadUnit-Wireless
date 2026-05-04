"""
Unit tests for tcp_server.main — graceful session restart logic.

Covered:
  on_aa_session_restart   — sends SHUTDOWN_REQUEST, waits for ack, deinits cryptor,
                            publishes aa.session.restarting
  on_ch0_frame            — sets _shutdown_ack_event only when _restart_pending=True
                            and payload carries msgId 0x000E

All external dependencies (BusClient, FrameRelay, TCPServer, AACryptor, get_logger)
are replaced with mocks so no network or ZMQ activity occurs.
"""

import struct
import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Path setup — make v2/ and v2/modules/ importable
# ---------------------------------------------------------------------------

_HERE    = Path(__file__).parent
_V2      = _HERE.parent
_MODULES = _V2 / "modules"

for p in (_V2, _MODULES):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ch0_payload(msg_id: int) -> str:
    """Build the payload_hex for a ch0 control frame with a 2-byte message ID."""
    return struct.pack(">H", msg_id).hex()


def _load_module():
    """Import tcp_server.main with all heavy dependencies mocked out.

    Returns the module object with a fresh global state.
    """
    # Build a minimal fake shared package if not already present
    fake_bus_client = MagicMock()
    fake_bus_instance = MagicMock()
    fake_bus_client.return_value = fake_bus_instance

    fake_bus_module = types.ModuleType("shared.bus_client")
    fake_bus_module.BusClient = fake_bus_client

    fake_logger_module = types.ModuleType("shared.logger")
    fake_logger_module.get_logger = MagicMock(return_value=MagicMock())

    fake_shared = types.ModuleType("shared")

    fake_server_module = types.ModuleType("tcp_server.server")
    fake_server_module.TCPServer = MagicMock()

    fake_relay_module = types.ModuleType("tcp_server.frame_relay")
    fake_relay_module.FrameRelay = MagicMock()

    fake_cryptor_cls = MagicMock()
    fake_cryptor_module = types.ModuleType("tcp_server.aa_cryptor")
    fake_cryptor_module.AACryptor = fake_cryptor_cls

    fake_tcp_server_pkg = types.ModuleType("tcp_server")

    mocks = {
        "shared":               fake_shared,
        "shared.bus_client":    fake_bus_module,
        "shared.logger":        fake_logger_module,
        "tcp_server":           fake_tcp_server_pkg,
        "tcp_server.server":    fake_server_module,
        "tcp_server.frame_relay": fake_relay_module,
        "tcp_server.aa_cryptor": fake_cryptor_module,
    }

    # Remove any previously imported version so we get fresh module state
    for key in list(sys.modules.keys()):
        if key in mocks or key == "tcp_server.main":
            del sys.modules[key]

    sys.modules.update(mocks)

    import tcp_server.main as mod
    return mod, fake_bus_instance, fake_cryptor_cls


# ---------------------------------------------------------------------------
# Tests: on_ch0_frame
# ---------------------------------------------------------------------------

class TestOnCh0Frame:
    def setup_method(self):
        self.mod, self.bus, self.cryptor_cls = _load_module()

    def test_ignores_frame_when_not_restart_pending(self):
        """on_ch0_frame must not signal the event when _restart_pending is False."""
        self.mod._restart_pending = False
        self.mod._shutdown_ack_event.clear()

        payload = _make_ch0_payload(self.mod._MSG_SHUTDOWN_RESPONSE)
        self.mod.on_ch0_frame("", {"payload_hex": payload})

        assert not self.mod._shutdown_ack_event.is_set()

    def test_ignores_wrong_msg_id_when_restart_pending(self):
        """on_ch0_frame must not signal the event for irrelevant message IDs."""
        self.mod._restart_pending = True
        self.mod._shutdown_ack_event.clear()

        payload = _make_ch0_payload(0x0001)  # some other msg
        self.mod.on_ch0_frame("", {"payload_hex": payload})

        assert not self.mod._shutdown_ack_event.is_set()

    def test_signals_event_on_shutdown_response(self):
        """on_ch0_frame MUST set _shutdown_ack_event when msgId is 0x000E and restart pending."""
        self.mod._restart_pending = True
        self.mod._shutdown_ack_event.clear()

        payload = _make_ch0_payload(self.mod._MSG_SHUTDOWN_RESPONSE)
        self.mod.on_ch0_frame("", {"payload_hex": payload})

        assert self.mod._shutdown_ack_event.is_set()

    def test_ignores_malformed_payload(self):
        """on_ch0_frame must not crash or signal on a non-hex payload."""
        self.mod._restart_pending = True
        self.mod._shutdown_ack_event.clear()

        self.mod.on_ch0_frame("", {"payload_hex": "ZZ"})
        assert not self.mod._shutdown_ack_event.is_set()

    def test_ignores_payload_too_short(self):
        """on_ch0_frame must not crash on a 1-byte payload (needs 2 bytes for msgId)."""
        self.mod._restart_pending = True
        self.mod._shutdown_ack_event.clear()

        self.mod.on_ch0_frame("", {"payload_hex": "ff"})
        assert not self.mod._shutdown_ack_event.is_set()


# ---------------------------------------------------------------------------
# Tests: on_aa_session_restart
# ---------------------------------------------------------------------------

class TestOnAaSessionRestart:
    def setup_method(self):
        self.mod, self.bus, self.cryptor_cls = _load_module()

    def _make_relay(self):
        relay = MagicMock()
        self.mod._relay = relay
        return relay

    def _make_cryptor(self):
        cryptor = MagicMock()
        self.mod._cryptor = cryptor
        return cryptor

    # ---- no active session ----

    def test_noop_when_no_relay(self):
        """Must return early and not publish anything if there is no active relay."""
        self.mod._relay = None
        self.mod.on_aa_session_restart("", {})
        self.bus.publish.assert_not_called()

    # ---- normal path: ack received in time ----

    def test_sends_shutdown_request(self):
        """Must write a SHUTDOWN_REQUEST frame to the relay."""
        relay = self._make_relay()
        self._make_cryptor()

        # Simulate SHUTDOWN_RESPONSE arriving immediately
        def side_effect(frame):
            payload_hex = _make_ch0_payload(self.mod._MSG_SHUTDOWN_RESPONSE)
            self.mod.on_ch0_frame("", {"payload_hex": payload_hex})
        relay.send_raw.side_effect = side_effect

        self.mod.on_aa_session_restart("", {})

        relay.send_raw.assert_called_once()
        raw_frame = relay.send_raw.call_args[0][0]
        # Frame header: channel=0, flags=0x00, len=2
        assert raw_frame[:4] == struct.pack(">BBH", 0, 0x00, 2)
        # Payload = SHUTDOWN_REQUEST msgId
        assert raw_frame[4:] == struct.pack(">H", self.mod._MSG_SHUTDOWN_REQUEST)

    def test_deinits_cryptor_after_ack(self):
        """cryptor.deinit() must be called after receiving the ack."""
        relay = self._make_relay()
        cryptor = self._make_cryptor()

        def side_effect(frame):
            payload_hex = _make_ch0_payload(self.mod._MSG_SHUTDOWN_RESPONSE)
            self.mod.on_ch0_frame("", {"payload_hex": payload_hex})
        relay.send_raw.side_effect = side_effect

        self.mod.on_aa_session_restart("", {})
        cryptor.deinit.assert_called_once()

    def test_publishes_session_restarting_after_ack(self):
        """aa.session.restarting must be published after ack + cryptor reset."""
        relay = self._make_relay()
        self._make_cryptor()

        def side_effect(frame):
            payload_hex = _make_ch0_payload(self.mod._MSG_SHUTDOWN_RESPONSE)
            self.mod.on_ch0_frame("", {"payload_hex": payload_hex})
        relay.send_raw.side_effect = side_effect

        self.mod.on_aa_session_restart("", {})
        self.bus.publish.assert_called_with("aa.session.restarting", {})

    def test_restart_pending_reset_to_false_after_completion(self):
        """_restart_pending must be False when on_aa_session_restart returns."""
        relay = self._make_relay()
        self._make_cryptor()

        def side_effect(frame):
            payload_hex = _make_ch0_payload(self.mod._MSG_SHUTDOWN_RESPONSE)
            self.mod.on_ch0_frame("", {"payload_hex": payload_hex})
        relay.send_raw.side_effect = side_effect

        self.mod.on_aa_session_restart("", {})
        assert self.mod._restart_pending is False

    # ---- timeout path: phone does not respond ----

    def test_proceeds_after_timeout(self):
        """Must still deinit cryptor and publish aa.session.restarting even if ack is never received."""
        relay = self._make_relay()
        cryptor = self._make_cryptor()

        # Override timeout to near-zero so the test is fast
        self.mod._SHUTDOWN_ACK_TIMEOUT = 0.05

        self.mod.on_aa_session_restart("", {})

        cryptor.deinit.assert_called_once()
        self.bus.publish.assert_called_with("aa.session.restarting", {})
        assert self.mod._restart_pending is False

    # ---- no cryptor (TLS never started) ----

    def test_proceeds_without_cryptor(self):
        """Must publish aa.session.restarting even when _cryptor is None."""
        relay = self._make_relay()
        self.mod._cryptor = None
        self.mod._SHUTDOWN_ACK_TIMEOUT = 0.05

        self.mod.on_aa_session_restart("", {})

        self.bus.publish.assert_called_with("aa.session.restarting", {})

    # ---- send_raw failure ----

    def test_aborts_on_send_failure(self):
        """If send_raw raises, must not set _restart_pending and must not publish."""
        relay = self._make_relay()
        relay.send_raw.side_effect = OSError("broken pipe")
        self._make_cryptor()

        self.mod.on_aa_session_restart("", {})

        self.bus.publish.assert_not_called()
        assert self.mod._restart_pending is False
