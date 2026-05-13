"""
Integration tests — config_manager  (Fase 2 §4)

Test what:  config_manager with a real in-process ZMQ bus + real YAML
            file I/O against a temporary CONFIG_DIR.

Pattern:
  - in_process_broker fixture (from conftest.py) — shared with other integration tests.
  - importlib.reload(cm_main) per test class setup — fresh bus, fresh _schemas dict.
  - Temporary directory (tmp_path) used as CONFIG_DIR to isolate filesystem state.
  - BusTracer mocked globally (no drain thread).
  - Spy BusClient receives published messages; _wait_received() does polling.

Groups:
  1. Boot protocol
  2. config.get — no-YAML paths (defaults, schema-seeding, empty)
  3. config.get — existing YAML path + schema echo
  4. config.set — happy path + config.changed
  5. config.set — schema validation (int, float, enum, bool, string)
  6. config.set — structured field (no scalar validation)
  7. Requester echo
  8. Malformed payloads (missing fields)
"""

from __future__ import annotations

import importlib
import sys
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers shared with other integration tests
# ---------------------------------------------------------------------------

def _make_client(broker, name: str):
    """Create a BusClient patched to use the in-process broker addresses."""
    import shared.bus_client as bc_mod
    importlib.reload(bc_mod)
    bc_mod.BROKER_PUB_ADDR = broker.pub_addr
    bc_mod.BROKER_SUB_ADDR = broker.sub_addr
    with patch("shared.bus_client.BusTracer", return_value=MagicMock()):
        client = bc_mod.BusClient(module_name=name)
    return client


def _start_client(client):
    t = threading.Thread(target=client.start, daemon=True)
    t.start()
    time.sleep(0.05)
    return t


def _wait_received(lst: list, count: int = 1, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(lst) >= count:
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------------------
# Module-level reload helper
# ---------------------------------------------------------------------------

def _load_cm(broker, config_dir: Path):
    """
    Reload config_manager.main, patch bus addresses + BusTracer,
    override CONFIG_DIR to a tmp path, and start the module bus.
    Returns (mod, bus_thread).
    """
    import shared.bus_client as bc_mod
    importlib.reload(bc_mod)
    bc_mod.BROKER_PUB_ADDR = broker.pub_addr
    bc_mod.BROKER_SUB_ADDR = broker.sub_addr

    with patch("shared.bus_client.BusTracer", return_value=MagicMock()):
        import config_manager.main as cm_mod
        importlib.reload(cm_mod)

    # Redirect filesystem I/O to tmp dir
    cm_mod.CONFIG_DIR = config_dir
    config_dir.mkdir(parents=True, exist_ok=True)

    bus_thread = threading.Thread(target=cm_mod.bus.start, daemon=True)
    bus_thread.start()
    time.sleep(0.05)
    return cm_mod


# ============================================================================
# 1 — Boot protocol
# ============================================================================

@pytest.mark.integration
class TestBootProtocol:

    def test_readytostart_publishes_module_ready(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_boot_1")
        received = []
        spy.subscribe("system.module_ready", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_system_readytostart()

        assert _wait_received(received)
        assert received[0]["name"] == "config_manager"
        assert received[0]["priority"] == 0
        spy.stop()

    def test_system_start_wrong_priority_ignored(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_boot_2")
        received = []
        spy.subscribe("system.ready", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_system_start("", {"priority": 99})  # wrong priority
        time.sleep(0.2)

        assert len(received) == 0
        spy.stop()

    def test_system_start_correct_priority_publishes_ready(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_boot_3")
        received = []
        spy.subscribe("system.ready", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_system_start("", {"priority": 0})

        assert _wait_received(received)
        assert received[0]["name"] == "config_manager"
        assert received[0]["priority"] == 0
        spy.stop()

    def test_system_start_creates_config_dir(self, in_process_broker, tmp_path):
        config_dir = tmp_path / "cfg_boot"
        cm = _load_cm(in_process_broker, config_dir)
        # Remove dir to verify it is re-created by system.start
        import shutil
        if config_dir.exists():
            shutil.rmtree(config_dir)

        cm.on_system_start("", {"priority": 0})
        time.sleep(0.1)

        assert config_dir.exists()


# ============================================================================
# 2 — config.get: no YAML paths
# ============================================================================

@pytest.mark.integration
class TestConfigGetNoYaml:

    def test_get_no_yaml_no_defaults_returns_empty(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_get_1")
        received = []
        spy.subscribe("config.response", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_get("", {"module": "mymod"})

        assert _wait_received(received)
        assert received[0]["module"] == "mymod"
        assert received[0]["config"] == {}
        spy.stop()

    def test_get_no_yaml_with_defaults_seeds_and_returns(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_get_2")
        received = []
        spy.subscribe("config.response", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.1)

        defaults = {"volume": 80, "enabled": True}
        cm.on_config_get("", {"module": "mymod", "defaults": defaults})

        assert _wait_received(received)
        assert received[0]["config"] == defaults
        # YAML file created
        assert (tmp_path / "mymod.yaml").exists()
        spy.stop()

    def test_get_no_yaml_schema_seeds_from_defaults(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_get_3")
        received = []
        spy.subscribe("config.response", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.1)

        schema = {
            "volume": {"type": "int", "default": 75, "min": 0, "max": 100},
            "enabled": {"type": "bool", "default": False},
        }
        cm.on_config_get("", {"module": "mymod_schema", "schema": schema})

        assert _wait_received(received)
        cfg = received[0]["config"]
        assert cfg["volume"] == 75
        assert cfg["enabled"] is False
        spy.stop()

    def test_get_seeds_yaml_only_once(self, in_process_broker, tmp_path):
        """Second get on existing YAML must NOT overwrite with defaults."""
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_get_4")
        received = []
        spy.subscribe("config.response", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.1)

        defaults = {"volume": 80}
        cm.on_config_get("", {"module": "once_mod", "defaults": defaults})
        assert _wait_received(received, 1)

        # Manually change the YAML
        yaml_path = tmp_path / "once_mod.yaml"
        yaml_path.write_text("volume: 55\n")

        cm.on_config_get("", {"module": "once_mod", "defaults": defaults})
        assert _wait_received(received, 2)

        assert received[1]["config"]["volume"] == 55
        spy.stop()


# ============================================================================
# 3 — config.get: existing YAML + schema echo
# ============================================================================

@pytest.mark.integration
class TestConfigGetExistingYaml:

    def _write_yaml(self, tmp_path, module: str, data: dict):
        p = tmp_path / f"{module}.yaml"
        p.write_text(yaml.safe_dump(data))

    def test_get_existing_yaml_returns_values(self, in_process_broker, tmp_path):
        self._write_yaml(tmp_path, "testmod", {"pin": "9999", "volume": 42})
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_exist_1")
        received = []
        spy.subscribe("config.response", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_get("", {"module": "testmod"})

        assert _wait_received(received)
        assert received[0]["config"]["pin"] == "9999"
        assert received[0]["config"]["volume"] == 42
        spy.stop()

    def test_get_echoes_schema_in_response(self, in_process_broker, tmp_path):
        self._write_yaml(tmp_path, "schmod", {"volume": 70})
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_exist_2")
        received = []
        spy.subscribe("config.response", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.1)

        schema = {"volume": {"type": "int", "default": 80, "min": 0, "max": 100}}
        cm.on_config_get("", {"module": "schmod", "schema": schema})

        assert _wait_received(received)
        assert "schema" in received[0]
        assert received[0]["schema"]["volume"]["type"] == "int"
        spy.stop()

    def test_schema_persists_across_second_get(self, in_process_broker, tmp_path):
        """Schema registered at first get must be echoed also in second get."""
        self._write_yaml(tmp_path, "persist_mod", {"volume": 70})
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_exist_3")
        received = []
        spy.subscribe("config.response", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.1)

        schema = {"volume": {"type": "int", "default": 80, "min": 0, "max": 100}}
        cm.on_config_get("", {"module": "persist_mod", "schema": schema})
        assert _wait_received(received, 1)

        cm.on_config_get("", {"module": "persist_mod"})
        assert _wait_received(received, 2)

        assert "schema" in received[1]
        spy.stop()

    def test_get_no_schema_no_schema_key_in_response(self, in_process_broker, tmp_path):
        self._write_yaml(tmp_path, "noschmod", {"x": 1})
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_exist_4")
        received = []
        spy.subscribe("config.response", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_get("", {"module": "noschmod"})

        assert _wait_received(received)
        assert "schema" not in received[0]
        spy.stop()


# ============================================================================
# 4 — config.set: happy path
# ============================================================================

@pytest.mark.integration
class TestConfigSetHappyPath:

    def test_set_creates_yaml_and_publishes_changed(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_set_1")
        changed = []
        spy.subscribe("config.changed", lambda t, p: changed.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_set("", {"module": "setmod", "key": "pin", "value": "4321"})

        assert _wait_received(changed)
        assert changed[0] == {"module": "setmod", "key": "pin", "value": "4321"}
        yaml_data = yaml.safe_load((tmp_path / "setmod.yaml").read_text())
        assert yaml_data["pin"] == "4321"
        spy.stop()

    def test_set_updates_existing_key(self, in_process_broker, tmp_path):
        (tmp_path / "updmod.yaml").write_text("volume: 50\n")
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_set_2")
        changed = []
        spy.subscribe("config.changed", lambda t, p: changed.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_set("", {"module": "updmod", "key": "volume", "value": 90})

        assert _wait_received(changed)
        yaml_data = yaml.safe_load((tmp_path / "updmod.yaml").read_text())
        assert yaml_data["volume"] == 90
        spy.stop()

    def test_set_adds_new_key_preserving_existing(self, in_process_broker, tmp_path):
        (tmp_path / "addmod.yaml").write_text("pin: '1234'\n")
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_set_3")
        changed = []
        spy.subscribe("config.changed", lambda t, p: changed.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_set("", {"module": "addmod", "key": "enabled", "value": True})

        assert _wait_received(changed)
        yaml_data = yaml.safe_load((tmp_path / "addmod.yaml").read_text())
        assert yaml_data["pin"] == "1234"  # preserved
        assert yaml_data["enabled"] is True  # added
        spy.stop()

    def test_set_get_roundtrip(self, in_process_broker, tmp_path):
        """set then get must return the updated value."""
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_set_4")
        responses = []
        spy.subscribe("config.response", lambda t, p: responses.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_set("", {"module": "rtmod", "key": "x", "value": 42})
        time.sleep(0.05)
        cm.on_config_get("", {"module": "rtmod"})

        assert _wait_received(responses)
        assert responses[0]["config"]["x"] == 42
        spy.stop()


# ============================================================================
# 5 — config.set: schema validation
# ============================================================================

@pytest.mark.integration
class TestConfigSetValidation:

    def _register_schema(self, cm, module: str, schema: dict):
        """Register schema via on_config_get without triggering file I/O."""
        cm._schemas[module] = cm.schema_from_dict(schema)

    def test_int_valid_coerces_string(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        changed = []
        error = []
        spy = _make_client(in_process_broker, "spy_val_1")
        spy.subscribe("config.changed", lambda t, p: changed.append(p))
        spy.subscribe("config.error",   lambda t, p: error.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_get("", {"module": "valmod", "schema": {
            "volume": {"type": "int", "default": 50, "min": 0, "max": 100}
        }})
        time.sleep(0.05)
        cm.on_config_set("", {"module": "valmod", "key": "volume", "value": "85"})

        assert _wait_received(changed)
        assert changed[0]["value"] == 85  # coerced to int
        assert len(error) == 0
        spy.stop()

    def test_int_below_min_publishes_error(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        changed = []
        error = []
        spy = _make_client(in_process_broker, "spy_val_2")
        spy.subscribe("config.changed", lambda t, p: changed.append(p))
        spy.subscribe("config.error",   lambda t, p: error.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_get("", {"module": "valmod2", "schema": {
            "volume": {"type": "int", "default": 50, "min": 0, "max": 100}
        }})
        time.sleep(0.05)
        cm.on_config_set("", {"module": "valmod2", "key": "volume", "value": -1})

        assert _wait_received(error)
        assert error[0]["key"] == "volume"
        assert "reason" in error[0]
        assert len(changed) == 0
        assert not (tmp_path / "valmod2.yaml").exists() or \
            yaml.safe_load((tmp_path / "valmod2.yaml").read_text()).get("volume") != -1
        spy.stop()

    def test_int_above_max_publishes_error(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        error = []
        spy = _make_client(in_process_broker, "spy_val_3")
        spy.subscribe("config.error", lambda t, p: error.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_get("", {"module": "valmod3", "schema": {
            "volume": {"type": "int", "default": 50, "min": 0, "max": 100}
        }})
        time.sleep(0.05)
        cm.on_config_set("", {"module": "valmod3", "key": "volume", "value": 150})

        assert _wait_received(error)
        assert "above maximum" in error[0]["reason"]
        spy.stop()

    def test_float_valid(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        changed = []
        spy = _make_client(in_process_broker, "spy_val_4")
        spy.subscribe("config.changed", lambda t, p: changed.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_get("", {"module": "fmod", "schema": {
            "gain": {"type": "float", "default": 1.0, "min": 0.0, "max": 2.0}
        }})
        time.sleep(0.05)
        cm.on_config_set("", {"module": "fmod", "key": "gain", "value": 1.5})

        assert _wait_received(changed)
        assert changed[0]["value"] == pytest.approx(1.5)
        spy.stop()

    def test_enum_valid(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        changed = []
        spy = _make_client(in_process_broker, "spy_val_5")
        spy.subscribe("config.changed", lambda t, p: changed.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_get("", {"module": "emod", "schema": {
            "mode": {"type": "enum", "default": "auto", "choices": ["off", "auto", "on"]}
        }})
        time.sleep(0.05)
        cm.on_config_set("", {"module": "emod", "key": "mode", "value": "on"})

        assert _wait_received(changed)
        assert changed[0]["value"] == "on"
        spy.stop()

    def test_enum_invalid_publishes_error(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        error = []
        spy = _make_client(in_process_broker, "spy_val_6")
        spy.subscribe("config.error", lambda t, p: error.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_get("", {"module": "emod2", "schema": {
            "mode": {"type": "enum", "default": "auto", "choices": ["off", "auto", "on"]}
        }})
        time.sleep(0.05)
        cm.on_config_set("", {"module": "emod2", "key": "mode", "value": "turbo"})

        assert _wait_received(error)
        assert "turbo" in error[0]["reason"] or "expected one of" in error[0]["reason"]
        spy.stop()

    def test_bool_coercion_from_string(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        changed = []
        spy = _make_client(in_process_broker, "spy_val_7")
        spy.subscribe("config.changed", lambda t, p: changed.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_get("", {"module": "bmod", "schema": {
            "enabled": {"type": "bool", "default": False}
        }})
        time.sleep(0.05)
        cm.on_config_set("", {"module": "bmod", "key": "enabled", "value": "true"})

        assert _wait_received(changed)
        assert changed[0]["value"] is True
        spy.stop()

    def test_string_no_schema_stored_as_is(self, in_process_broker, tmp_path):
        """Key with no schema registered must be persisted without error."""
        cm = _load_cm(in_process_broker, tmp_path)
        changed = []
        spy = _make_client(in_process_broker, "spy_val_8")
        spy.subscribe("config.changed", lambda t, p: changed.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_set("", {"module": "strmod", "key": "label", "value": "hello"})

        assert _wait_received(changed)
        assert changed[0]["value"] == "hello"
        spy.stop()


# ============================================================================
# 6 — config.set: structured field (no scalar validation)
# ============================================================================

@pytest.mark.integration
class TestConfigSetStructuredField:

    def test_list_field_stored_as_is(self, in_process_broker, tmp_path):
        """ConfigFieldList in schema must skip scalar validation and persist the list."""
        cm = _load_cm(in_process_broker, tmp_path)
        changed = []
        error = []
        spy = _make_client(in_process_broker, "spy_struct_1")
        spy.subscribe("config.changed", lambda t, p: changed.append(p))
        spy.subscribe("config.error",   lambda t, p: error.append(p))
        _start_client(spy)
        time.sleep(0.1)

        schema = {
            "channels": {
                "type": "list",
                "item_schema": {"type": "message", "optional": False, "fields": {
                    "id": {"type": "int", "default": 0},
                }},
                "default": [],
            }
        }
        cm.on_config_get("", {"module": "structmod", "schema": schema})
        time.sleep(0.05)

        channels_value = [{"id": 1}, {"id": 2}]
        cm.on_config_set("", {"module": "structmod", "key": "channels", "value": channels_value})

        assert _wait_received(changed)
        assert changed[0]["value"] == channels_value
        assert len(error) == 0
        spy.stop()

    def test_message_field_stored_as_is(self, in_process_broker, tmp_path):
        """ConfigFieldMessage in schema must skip scalar validation."""
        cm = _load_cm(in_process_broker, tmp_path)
        changed = []
        error = []
        spy = _make_client(in_process_broker, "spy_struct_2")
        spy.subscribe("config.changed", lambda t, p: changed.append(p))
        spy.subscribe("config.error",   lambda t, p: error.append(p))
        _start_client(spy)
        time.sleep(0.1)

        schema = {
            "wifi": {
                "type": "message",
                "optional": True,
                "fields": {
                    "ssid": {"type": "string", "default": ""},
                }
            }
        }
        cm.on_config_get("", {"module": "msgmod", "schema": schema})
        time.sleep(0.05)

        wifi_value = {"ssid": "MyWifi", "password": "secret"}
        cm.on_config_set("", {"module": "msgmod", "key": "wifi", "value": wifi_value})

        assert _wait_received(changed)
        assert changed[0]["value"] == wifi_value
        assert len(error) == 0
        spy.stop()


# ============================================================================
# 7 — Requester echo
# ============================================================================

@pytest.mark.integration
class TestRequesterEcho:

    def test_requester_echoed_in_response(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_req_1")
        received = []
        spy.subscribe("config.response", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_get("", {"module": "reqmod", "requester": "audio_manager"})

        assert _wait_received(received)
        assert received[0]["requester"] == "audio_manager"
        spy.stop()

    def test_requester_defaults_to_empty_string(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_req_2")
        received = []
        spy.subscribe("config.response", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_get("", {"module": "reqmod2"})

        assert _wait_received(received)
        assert received[0]["requester"] == ""
        spy.stop()

    def test_multiple_requesters_receive_all_responses(self, in_process_broker, tmp_path):
        """All subscribers to config.response see the message; each filters by requester."""
        cm = _load_cm(in_process_broker, tmp_path)
        spy_a = _make_client(in_process_broker, "spy_req_3a")
        spy_b = _make_client(in_process_broker, "spy_req_3b")
        recv_a, recv_b = [], []
        spy_a.subscribe("config.response", lambda t, p: recv_a.append(p))
        spy_b.subscribe("config.response", lambda t, p: recv_b.append(p))
        _start_client(spy_a)
        _start_client(spy_b)
        time.sleep(0.15)

        cm.on_config_get("", {"module": "shared_mod", "requester": "audio_manager"})

        assert _wait_received(recv_a) and _wait_received(recv_b)
        assert recv_a[0]["requester"] == "audio_manager"
        assert recv_b[0]["requester"] == "audio_manager"
        spy_a.stop()
        spy_b.stop()


# ============================================================================
# 8 — Malformed payloads
# ============================================================================

@pytest.mark.integration
class TestMalformedPayloads:

    def test_get_missing_module_no_response(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_mal_1")
        received = []
        spy.subscribe("config.response", lambda t, p: received.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_get("", {})
        time.sleep(0.2)

        assert len(received) == 0
        spy.stop()

    def test_set_missing_module_no_changed(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_mal_2")
        changed = []
        spy.subscribe("config.changed", lambda t, p: changed.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_set("", {"key": "pin", "value": "0000"})
        time.sleep(0.2)

        assert len(changed) == 0
        spy.stop()

    def test_set_missing_key_no_changed(self, in_process_broker, tmp_path):
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_mal_3")
        changed = []
        spy.subscribe("config.changed", lambda t, p: changed.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_set("", {"module": "malmod", "value": "whatever"})
        time.sleep(0.2)

        assert len(changed) == 0
        spy.stop()

    def test_set_empty_payload_no_crash(self, in_process_broker, tmp_path):
        """Empty payload must not raise — module must remain stable."""
        cm = _load_cm(in_process_broker, tmp_path)
        spy = _make_client(in_process_broker, "spy_mal_4")
        changed = []
        spy.subscribe("config.changed", lambda t, p: changed.append(p))
        _start_client(spy)
        time.sleep(0.1)

        cm.on_config_set("", {})
        time.sleep(0.2)

        assert len(changed) == 0
        spy.stop()
