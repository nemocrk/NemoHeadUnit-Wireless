"""
Unit tests for bluetooth/pairing.py — PairingAgent

Strategy:
  pairing.py importa dbus + dbus.service + dbus.exceptions in modo lazy
  (dentro i metodi). Il modulo viene importato una volta sola a livello
  di file con stub in sys.modules e get_logger patchato.

  I test evitano di creare _DBusAgent reale (richiede dbus.service.Object)
  iniettando un mock direttamente in agent._dbus_agent.
  I metodi interni (_handle_pin_code_request, _handle_confirm_request,
  _confirm_worker, ecc.) vengono chiamati direttamente.

Covers:
  Section 1  — __init__: stato iniziale
  Section 2  — register(): successo → True + _registered, eccezione → False
  Section 3  — unregister(): not registered skip, chiamata normale, eccezione non propagata
  Section 4  — pair(): device not found → on_pairing_failed,
               dispatch ok → Pair() chiamato, eccezione setup → on_pairing_failed
  Section 5  — _pair_reply_handler(): on_pairing_completed chiamato, _trust_device chiamato
  Section 6  — _pair_error_handler(): AlreadyExists → success, altro → on_pairing_failed
  Section 7  — confirm(): address mismatch → False, PIN mismatch → False + event set,
               happy path → True + _confirm_accepted=True + event set
  Section 8  — reject(): _confirm_accepted=False + event set
  Section 9  — _handle_pin_code_request(): genera PIN 4 cifre, chiama on_pin_requested,
               ritorna PIN, _pending_address settato
  Section 10 — _handle_confirm_request(): _pending_pin/address settati, on_pin_requested
               chiamato, thread lanciato
  Section 11 — _confirm_worker(): utente conferma → reply_handler(), utente rifiuta →
               error_handler(Rejected), timeout → auto-accept reply_handler(),
               cleanup callbacks dopo esecuzione
  Section 12 — _handle_cancel(): _confirm_accepted=False, event set
  Section 13 — _resolve_device_path(): trovato, non trovato, eccezione → None
  Section 14 — _path_to_address(): conversione path → MAC
  Section 15 — _trust_device(): Props.Set chiamato, eccezione non propagata
"""

from __future__ import annotations

import sys
import types
import importlib
import threading
from unittest.mock import MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

def _install_stubs():
    dbus_mod = types.ModuleType("dbus")
    dbus_mod.Interface   = MagicMock
    dbus_mod.Boolean     = lambda v: v
    dbus_mod.UInt32      = lambda v: v

    dbus_service = types.ModuleType("dbus.service")
    dbus_service.Object = object
    dbus_service.method = lambda *a, **kw: (lambda f: f)

    dbus_exc = types.ModuleType("dbus.exceptions")
    dbus_exc.DBusException = Exception

    sys.modules.setdefault("dbus", dbus_mod)
    sys.modules.setdefault("dbus.service", dbus_service)
    sys.modules.setdefault("dbus.exceptions", dbus_exc)

    gi_mod = types.ModuleType("gi")
    gi_mod.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mod)
    gi_repo = types.ModuleType("gi.repository")
    gi_repo.GLib = MagicMock()
    sys.modules.setdefault("gi.repository", gi_repo)
    sys.modules.setdefault("gi.repository.GLib", MagicMock())


_install_stubs()

for _k in list(sys.modules.keys()):
    if "pairing" in _k and "bluetooth" in _k:
        del sys.modules[_k]

with patch("shared.logger.get_logger", return_value=MagicMock()):
    import modules.bluetooth.pairing as _pair_mod
    importlib.reload(_pair_mod)

PairingAgent = _pair_mod.PairingAgent
AUTO_ACCEPT_TIMEOUT_S = _pair_mod.AUTO_ACCEPT_TIMEOUT_S


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter_bus(objects=None):
    """Returns a mock adapter with a mock bus that returns given objects."""
    mock_bus = MagicMock()
    mock_adapter = MagicMock()
    mock_adapter._bus = mock_bus
    mock_manager = MagicMock()
    mock_manager.GetManagedObjects.return_value = objects or {}

    mock_dbus = MagicMock()
    mock_dbus.Interface.return_value = mock_manager
    mock_dbus.Boolean = lambda v: v

    return mock_adapter, mock_dbus


def _fresh_agent(objects=None, **cbs):
    mock_adapter, mock_dbus = _make_adapter_bus(objects)
    agent = PairingAgent(
        mock_adapter,
        on_pin_requested=cbs.get("on_pin_requested"),
        on_pairing_completed=cbs.get("on_pairing_completed"),
        on_pairing_failed=cbs.get("on_pairing_failed"),
    )
    agent._dbus_agent = MagicMock()   # skip _DBusAgent real creation
    return agent, mock_adapter, mock_dbus


def _objects_with_device(address, path=None):
    path = path or f"/org/bluez/hci0/dev_{address.replace(':', '_')}"
    return {
        path: {"org.bluez.Device1": {"Address": address}}
    }


# ===========================================================================
# Section 1 — __init__
# ===========================================================================

class TestInit:

    @pytest.mark.unit
    def test_pending_pin_empty(self):
        agent, *_ = _fresh_agent()
        assert agent._pending_pin == ""

    @pytest.mark.unit
    def test_pending_address_empty(self):
        agent, *_ = _fresh_agent()
        assert agent._pending_address == ""

    @pytest.mark.unit
    def test_registered_is_false(self):
        agent, *_ = _fresh_agent()
        assert agent._registered is False

    @pytest.mark.unit
    def test_confirm_accepted_is_false(self):
        agent, *_ = _fresh_agent()
        assert agent._confirm_accepted is False

    @pytest.mark.unit
    def test_dbus_reply_handler_is_none(self):
        agent, *_ = _fresh_agent()
        assert agent._dbus_reply_handler is None


# ===========================================================================
# Section 2 — register()
# ===========================================================================

class TestRegister:

    @pytest.mark.unit
    def test_returns_true_on_success(self):
        agent, mock_adapter, mock_dbus = _fresh_agent()
        with patch.dict(sys.modules, {"dbus": mock_dbus, "dbus.service": sys.modules["dbus.service"]}):
            result = agent.register()
        assert result is True

    @pytest.mark.unit
    def test_sets_registered(self):
        agent, mock_adapter, mock_dbus = _fresh_agent()
        with patch.dict(sys.modules, {"dbus": mock_dbus, "dbus.service": sys.modules["dbus.service"]}):
            agent.register()
        assert agent._registered is True

    @pytest.mark.unit
    def test_returns_false_on_exception(self):
        agent, mock_adapter, mock_dbus = _fresh_agent()
        mock_dbus.Interface.side_effect = Exception("D-Bus error")
        with patch.dict(sys.modules, {"dbus": mock_dbus, "dbus.service": sys.modules["dbus.service"]}):
            result = agent.register()
        assert result is False

    @pytest.mark.unit
    def test_registered_false_on_exception(self):
        agent, mock_adapter, mock_dbus = _fresh_agent()
        mock_dbus.Interface.side_effect = Exception("D-Bus error")
        with patch.dict(sys.modules, {"dbus": mock_dbus, "dbus.service": sys.modules["dbus.service"]}):
            agent.register()
        assert agent._registered is False


# ===========================================================================
# Section 3 — unregister()
# ===========================================================================

class TestUnregister:

    @pytest.mark.unit
    def test_skips_when_not_registered(self):
        agent, mock_adapter, mock_dbus = _fresh_agent()
        agent._registered = False
        mock_mgr = MagicMock()
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            agent.unregister()
        mock_dbus.Interface.assert_not_called()

    @pytest.mark.unit
    def test_calls_unregister_agent_when_registered(self):
        agent, mock_adapter, mock_dbus = _fresh_agent()
        agent._registered = True
        mock_mgr = MagicMock()
        mock_dbus.Interface.return_value = mock_mgr
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            agent.unregister()
        mock_mgr.UnregisterAgent.assert_called_once()

    @pytest.mark.unit
    def test_sets_registered_false(self):
        agent, mock_adapter, mock_dbus = _fresh_agent()
        agent._registered = True
        mock_dbus.Interface.return_value = MagicMock()
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            agent.unregister()
        assert agent._registered is False

    @pytest.mark.unit
    def test_exception_not_propagated(self):
        agent, mock_adapter, mock_dbus = _fresh_agent()
        agent._registered = True
        mock_dbus.Interface.side_effect = Exception("D-Bus error")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            agent.unregister()  # must not raise


# ===========================================================================
# Section 4 — pair()
# ===========================================================================

class TestPair:

    @pytest.mark.unit
    def test_device_not_found_calls_on_pairing_failed(self):
        cb = MagicMock()
        agent, mock_adapter, mock_dbus = _fresh_agent(objects={}, on_pairing_failed=cb)
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            agent.pair("AA:BB:CC:DD:EE:FF")
        cb.assert_called_once()
        assert "AA:BB:CC:DD:EE:FF" in cb.call_args.args[0]

    @pytest.mark.unit
    def test_pair_dispatches_device_pair(self):
        address = "AA:BB:CC:DD:EE:FF"
        objects = _objects_with_device(address)
        agent, mock_adapter, mock_dbus = _fresh_agent(objects=objects)
        mock_device_iface = MagicMock()
        mock_dbus.Interface.return_value = mock_device_iface
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            agent.pair(address)
        mock_device_iface.Pair.assert_called_once()

    @pytest.mark.unit
    def test_pair_sets_pending_address(self):
        address = "AA:BB:CC:DD:EE:FF"
        objects = _objects_with_device(address)
        agent, mock_adapter, mock_dbus = _fresh_agent(objects=objects)
        mock_dbus.Interface.return_value = MagicMock()
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            agent.pair(address)
        assert agent._pending_address == address

    @pytest.mark.unit
    def test_exception_during_setup_calls_on_pairing_failed(self):
        cb = MagicMock()
        address = "AA:BB:CC:DD:EE:FF"
        objects = _objects_with_device(address)
        agent, mock_adapter, mock_dbus = _fresh_agent(objects=objects, on_pairing_failed=cb)
        mock_dbus.Interface.side_effect = Exception("setup failed")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            agent.pair(address)
        cb.assert_called_once()


# ===========================================================================
# Section 5 — _pair_reply_handler()
# ===========================================================================

class TestPairReplyHandler:

    @pytest.mark.unit
    def test_calls_on_pairing_completed(self):
        cb = MagicMock()
        agent, mock_adapter, mock_dbus = _fresh_agent(on_pairing_completed=cb)
        with patch.object(agent, "_trust_device"):
            agent._pair_reply_handler("AA:BB")
        cb.assert_called_once_with("AA:BB")

    @pytest.mark.unit
    def test_calls_trust_device(self):
        agent, mock_adapter, mock_dbus = _fresh_agent()
        with patch.object(agent, "_trust_device") as mock_trust:
            agent._pair_reply_handler("AA:BB")
        mock_trust.assert_called_once_with("AA:BB")


# ===========================================================================
# Section 6 — _pair_error_handler()
# ===========================================================================

class TestPairErrorHandler:

    @pytest.mark.unit
    def test_already_exists_calls_on_pairing_completed(self):
        cb = MagicMock()
        agent, mock_adapter, mock_dbus = _fresh_agent(on_pairing_completed=cb)
        with patch.object(agent, "_trust_device"):
            agent._pair_error_handler("AA:BB", Exception("AlreadyExists"))
        cb.assert_called_once_with("AA:BB")

    @pytest.mark.unit
    def test_already_exists_calls_trust_device(self):
        agent, mock_adapter, mock_dbus = _fresh_agent()
        with patch.object(agent, "_trust_device") as mock_trust:
            agent._pair_error_handler("AA:BB", Exception("AlreadyExists"))
        mock_trust.assert_called_once()

    @pytest.mark.unit
    def test_other_error_calls_on_pairing_failed(self):
        cb = MagicMock()
        agent, mock_adapter, mock_dbus = _fresh_agent(on_pairing_failed=cb)
        agent._pair_error_handler("AA:BB", Exception("generic error"))
        cb.assert_called_once()
        assert "AA:BB" == cb.call_args.args[0]

    @pytest.mark.unit
    def test_other_error_no_on_pairing_failed_no_crash(self):
        agent, mock_adapter, mock_dbus = _fresh_agent()
        agent._pair_error_handler("AA:BB", Exception("generic error"))  # must not raise


# ===========================================================================
# Section 7 — confirm()
# ===========================================================================

class TestConfirm:

    @pytest.mark.unit
    def test_address_mismatch_returns_false(self):
        agent, *_ = _fresh_agent()
        agent._pending_address = "BB:BB"
        result = agent.confirm("AA:AA", "123456")
        assert result is False

    @pytest.mark.unit
    def test_pin_mismatch_returns_false(self):
        agent, *_ = _fresh_agent()
        agent._pending_address = "AA:BB"
        agent._pending_pin = "123456"
        result = agent.confirm("AA:BB", "999999")
        assert result is False

    @pytest.mark.unit
    def test_pin_mismatch_sets_confirm_event(self):
        agent, *_ = _fresh_agent()
        agent._pending_address = "AA:BB"
        agent._pending_pin = "123456"
        agent.confirm("AA:BB", "999999")
        assert agent._confirm_event.is_set()

    @pytest.mark.unit
    def test_happy_path_returns_true(self):
        agent, *_ = _fresh_agent()
        agent._pending_address = "AA:BB"
        agent._pending_pin = "123456"
        result = agent.confirm("AA:BB", "123456")
        assert result is True

    @pytest.mark.unit
    def test_happy_path_sets_confirm_accepted(self):
        agent, *_ = _fresh_agent()
        agent._pending_address = "AA:BB"
        agent._pending_pin = "123456"
        agent.confirm("AA:BB", "123456")
        assert agent._confirm_accepted is True

    @pytest.mark.unit
    def test_happy_path_sets_event(self):
        agent, *_ = _fresh_agent()
        agent._pending_address = "AA:BB"
        agent._pending_pin = "123456"
        agent.confirm("AA:BB", "123456")
        assert agent._confirm_event.is_set()


# ===========================================================================
# Section 8 — reject()
# ===========================================================================

class TestReject:

    @pytest.mark.unit
    def test_sets_confirm_accepted_false(self):
        agent, *_ = _fresh_agent()
        agent._confirm_accepted = True
        agent.reject("AA:BB")
        assert agent._confirm_accepted is False

    @pytest.mark.unit
    def test_sets_confirm_event(self):
        agent, *_ = _fresh_agent()
        agent.reject("AA:BB")
        assert agent._confirm_event.is_set()


# ===========================================================================
# Section 9 — _handle_pin_code_request()
# ===========================================================================

class TestHandlePinCodeRequest:

    @pytest.mark.unit
    def test_returns_4_digit_string(self):
        agent, *_ = _fresh_agent()
        pin = agent._handle_pin_code_request("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        assert len(pin) == 4 and pin.isdigit()

    @pytest.mark.unit
    def test_calls_on_pin_requested(self):
        cb = MagicMock()
        agent, mock_adapter, mock_dbus = _fresh_agent(on_pin_requested=cb)
        agent._handle_pin_code_request("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        cb.assert_called_once()

    @pytest.mark.unit
    def test_sets_pending_address(self):
        agent, *_ = _fresh_agent()
        agent._handle_pin_code_request("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        assert agent._pending_address == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.unit
    def test_stores_pending_pin(self):
        agent, *_ = _fresh_agent()
        pin = agent._handle_pin_code_request("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        assert agent._pending_pin == pin


# ===========================================================================
# Section 10 — _handle_confirm_request()
# ===========================================================================

class TestHandleConfirmRequest:

    @pytest.mark.unit
    def test_sets_pending_pin_from_passkey(self):
        agent, *_ = _fresh_agent()
        reply_h, error_h = MagicMock(), MagicMock()
        agent._handle_confirm_request("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", 123456, reply_h, error_h)
        assert agent._pending_pin == "123456"

    @pytest.mark.unit
    def test_sets_pending_address(self):
        agent, *_ = _fresh_agent()
        reply_h, error_h = MagicMock(), MagicMock()
        agent._handle_confirm_request("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", 123456, reply_h, error_h)
        assert agent._pending_address == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.unit
    def test_calls_on_pin_requested(self):
        cb = MagicMock()
        agent, mock_adapter, mock_dbus = _fresh_agent(on_pin_requested=cb)
        agent._handle_confirm_request("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", 123456, MagicMock(), MagicMock())
        cb.assert_called_once()

    @pytest.mark.unit
    def test_stores_dbus_reply_handler(self):
        agent, *_ = _fresh_agent()
        reply_h = MagicMock()
        agent._handle_confirm_request("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", 123456, reply_h, MagicMock())
        assert agent._dbus_reply_handler is reply_h

    @pytest.mark.unit
    def test_launches_confirm_worker_thread(self):
        agent, *_ = _fresh_agent()
        with patch("threading.Thread") as mock_t:
            mock_t.return_value.start = MagicMock()
            agent._handle_confirm_request("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", 123456, MagicMock(), MagicMock())
        mock_t.return_value.start.assert_called_once()


# ===========================================================================
# Section 11 — _confirm_worker()
# ===========================================================================

class TestConfirmWorker:

    @pytest.mark.unit
    def test_user_confirm_calls_reply_handler(self):
        agent, *_ = _fresh_agent()
        reply_h = MagicMock()
        agent._dbus_reply_handler = reply_h
        agent._dbus_error_handler = MagicMock()
        agent._confirm_accepted = True
        agent._confirm_event.set()
        agent._confirm_worker("AA:BB")
        reply_h.assert_called_once()

    @pytest.mark.unit
    def test_user_reject_calls_error_handler(self):
        agent, *_ = _fresh_agent()
        error_h = MagicMock()
        agent._dbus_reply_handler = MagicMock()
        agent._dbus_error_handler = error_h
        agent._confirm_accepted = False
        agent._confirm_event.set()
        with patch.dict(sys.modules["dbus.exceptions"].__dict__, {"DBusException": Exception}):
            agent._confirm_worker("AA:BB")
        error_h.assert_called_once()

    @pytest.mark.unit
    def test_timeout_auto_accepts(self):
        agent, *_ = _fresh_agent()
        reply_h = MagicMock()
        agent._dbus_reply_handler = reply_h
        agent._dbus_error_handler = MagicMock()
        agent._confirm_accepted = False
        # Don't set the event -> timeout will fire
        with patch.object(agent._confirm_event, "wait", return_value=False):
            agent._confirm_worker("AA:BB")
        assert agent._confirm_accepted is True
        reply_h.assert_called_once()

    @pytest.mark.unit
    def test_cleanup_reply_handler_after_execution(self):
        agent, *_ = _fresh_agent()
        agent._dbus_reply_handler = MagicMock()
        agent._dbus_error_handler = MagicMock()
        agent._confirm_accepted = True
        agent._confirm_event.set()
        agent._confirm_worker("AA:BB")
        assert agent._dbus_reply_handler is None
        assert agent._dbus_error_handler is None


# ===========================================================================
# Section 12 — _handle_cancel()
# ===========================================================================

class TestHandleCancel:

    @pytest.mark.unit
    def test_sets_confirm_accepted_false(self):
        agent, *_ = _fresh_agent()
        agent._confirm_accepted = True
        agent._handle_cancel()
        assert agent._confirm_accepted is False

    @pytest.mark.unit
    def test_sets_confirm_event(self):
        agent, *_ = _fresh_agent()
        agent._handle_cancel()
        assert agent._confirm_event.is_set()


# ===========================================================================
# Section 13 — _resolve_device_path()
# ===========================================================================

class TestResolveDevicePath:

    @pytest.mark.unit
    def test_returns_path_when_found(self):
        address = "AA:BB:CC:DD:EE:FF"
        objects = _objects_with_device(address, "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        agent, mock_adapter, mock_dbus = _fresh_agent(objects=objects)
        mock_mgr = MagicMock()
        mock_mgr.GetManagedObjects.return_value = objects
        mock_dbus.Interface.return_value = mock_mgr
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = agent._resolve_device_path(address)
        assert result == "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"

    @pytest.mark.unit
    def test_returns_none_when_not_found(self):
        agent, mock_adapter, mock_dbus = _fresh_agent(objects={})
        mock_mgr = MagicMock()
        mock_mgr.GetManagedObjects.return_value = {}
        mock_dbus.Interface.return_value = mock_mgr
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = agent._resolve_device_path("AA:BB")
        assert result is None

    @pytest.mark.unit
    def test_returns_none_on_exception(self):
        agent, mock_adapter, mock_dbus = _fresh_agent()
        mock_dbus.Interface.side_effect = Exception("D-Bus error")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            result = agent._resolve_device_path("AA:BB")
        assert result is None


# ===========================================================================
# Section 14 — _path_to_address()
# ===========================================================================

class TestPathToAddress:

    @pytest.mark.unit
    def test_converts_path_to_mac(self):
        agent, *_ = _fresh_agent()
        result = agent._path_to_address("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        assert result == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.unit
    def test_handles_short_path(self):
        agent, *_ = _fresh_agent()
        result = agent._path_to_address("/org/bluez/hci0/dev_11_22_33_44_55_66")
        assert result == "11:22:33:44:55:66"


# ===========================================================================
# Section 15 — _trust_device()
# ===========================================================================

class TestTrustDevice:

    @pytest.mark.unit
    def test_calls_props_set(self):
        address = "AA:BB:CC:DD:EE:FF"
        objects = _objects_with_device(address)
        agent, mock_adapter, mock_dbus = _fresh_agent(objects=objects)
        mock_mgr = MagicMock()
        mock_mgr.GetManagedObjects.return_value = objects
        mock_props = MagicMock()
        call_count = [0]
        def iface_factory(obj, iface_name):
            call_count[0] += 1
            if iface_name == "org.freedesktop.DBus.ObjectManager":
                return mock_mgr
            return mock_props
        mock_dbus.Interface.side_effect = iface_factory
        mock_dbus.Boolean = lambda v: v
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            agent._trust_device(address)
        mock_props.Set.assert_called_once()

    @pytest.mark.unit
    def test_exception_not_propagated(self):
        address = "AA:BB:CC:DD:EE:FF"
        agent, mock_adapter, mock_dbus = _fresh_agent()
        mock_dbus.Interface.side_effect = Exception("D-Bus error")
        with patch.dict(sys.modules, {"dbus": mock_dbus}):
            agent._trust_device(address)  # must not raise
