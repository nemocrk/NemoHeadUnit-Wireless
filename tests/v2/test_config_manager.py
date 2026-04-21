"""
Unit tests for v2/modules/config_manager/main.py

Tested behaviours:
  - config.get on missing module returns empty dict via config.response
  - config.set persists value to YAML and publishes config.changed
  - config.set then config.get returns the stored value
  - config.set with missing 'key' is silently ignored
  - config.get with missing 'module' is silently ignored
  - multiple keys accumulate in the same YAML file
  - config.set overwrites an existing key
"""

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
import yaml

# ---------------------------------------------------------------------------
# Path setup — mirror what the module does at import time
# ---------------------------------------------------------------------------

_TESTS = Path(__file__).parent          # tests/v2/
_ROOT  = _TESTS.parent.parent           # repo root
_V2    = _ROOT / "v2"
_MODULES = _V2 / "modules"

for p in (str(_V2), str(_MODULES)):
    if p not in sys.path:
        sys.path.insert(0, p)

# ---------------------------------------------------------------------------
# Stub shared.bus_client so the module loads without a live ZMQ broker
# ---------------------------------------------------------------------------

_bus_stub = MagicMock()
_bus_stub_instance = MagicMock()
_bus_stub.return_value = _bus_stub_instance

shared_pkg = types.ModuleType("shared")
bus_client_mod = types.ModuleType("shared.bus_client")
bus_client_mod.BusClient = _bus_stub
logger_mod = types.ModuleType("shared.logger")
logger_mod.get_logger = MagicMock(return_value=MagicMock())

sys.modules.setdefault("shared", shared_pkg)
sys.modules["shared.bus_client"] = bus_client_mod
sys.modules["shared.logger"] = logger_mod

# ---------------------------------------------------------------------------
# Import the module under test AFTER stubs are in place
# ---------------------------------------------------------------------------

import importlib
import v2.modules.config_manager.main as cm  # noqa: E402  (after sys.path setup)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_config_dir(tmp_path, monkeypatch):
    """Redirect CONFIG_DIR to a temporary directory for every test."""
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path)
    yield tmp_path


@pytest.fixture(autouse=True)
def reset_bus_publish():
    """Clear publish call history before each test."""
    cm.bus.publish.reset_mock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _published_payloads(topic: str) -> list[dict]:
    """Return all payload dicts published on *topic*."""
    return [
        args[1]
        for args, _ in cm.bus.publish.call_args_list
        if args[0] == topic
    ]


# ---------------------------------------------------------------------------
# Tests — config.get
# ---------------------------------------------------------------------------

class TestConfigGet:
    def test_missing_module_field_is_ignored(self):
        cm.on_config_get("config.get", {})
        cm.bus.publish.assert_not_called()

    def test_unknown_module_returns_empty_config(self):
        cm.on_config_get("config.get", {"module": "nonexistent"})
        payloads = _published_payloads("config.response")
        assert len(payloads) == 1
        assert payloads[0] == {"module": "nonexistent", "config": {}}

    def test_returns_existing_config(self, tmp_config_dir):
        (tmp_config_dir / "mymod.yaml").write_text("foo: bar\n")
        cm.on_config_get("config.get", {"module": "mymod"})
        payloads = _published_payloads("config.response")
        assert payloads[0]["config"] == {"foo": "bar"}

    def test_corrupted_yaml_returns_empty_config(self, tmp_config_dir):
        (tmp_config_dir / "bad.yaml").write_text(": invalid: yaml: [[\n")
        cm.on_config_get("config.get", {"module": "bad"})
        payloads = _published_payloads("config.response")
        assert payloads[0]["config"] == {}


# ---------------------------------------------------------------------------
# Tests — config.set
# ---------------------------------------------------------------------------

class TestConfigSet:
    def test_missing_module_is_ignored(self):
        cm.on_config_set("config.set", {"key": "x", "value": 1})
        cm.bus.publish.assert_not_called()

    def test_missing_key_is_ignored(self):
        cm.on_config_set("config.set", {"module": "m"})
        cm.bus.publish.assert_not_called()

    def test_persists_value_to_yaml(self, tmp_config_dir):
        cm.on_config_set("config.set", {"module": "bt", "key": "pin", "value": "9999"})
        data = yaml.safe_load((tmp_config_dir / "bt.yaml").read_text())
        assert data == {"pin": "9999"}

    def test_publishes_config_changed(self):
        cm.on_config_set("config.set", {"module": "bt", "key": "pin", "value": "9999"})
        payloads = _published_payloads("config.changed")
        assert len(payloads) == 1
        assert payloads[0] == {"module": "bt", "key": "pin", "value": "9999"}

    def test_multiple_keys_accumulate(self, tmp_config_dir):
        cm.on_config_set("config.set", {"module": "m", "key": "a", "value": 1})
        cm.on_config_set("config.set", {"module": "m", "key": "b", "value": 2})
        data = yaml.safe_load((tmp_config_dir / "m.yaml").read_text())
        assert data == {"a": 1, "b": 2}

    def test_overwrites_existing_key(self, tmp_config_dir):
        cm.on_config_set("config.set", {"module": "m", "key": "x", "value": "old"})
        cm.on_config_set("config.set", {"module": "m", "key": "x", "value": "new"})
        data = yaml.safe_load((tmp_config_dir / "m.yaml").read_text())
        assert data["x"] == "new"

    def test_set_then_get_roundtrip(self, tmp_config_dir):
        cm.on_config_set("config.set", {"module": "rt", "key": "timeout", "value": 30})
        cm.bus.publish.reset_mock()
        cm.on_config_get("config.get", {"module": "rt"})
        payloads = _published_payloads("config.response")
        assert payloads[0]["config"] == {"timeout": 30}


# ---------------------------------------------------------------------------
# Tests — lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_system_start_creates_config_dir(self, tmp_path, monkeypatch):
        target = tmp_path / "nested" / "config"
        monkeypatch.setattr(cm, "CONFIG_DIR", target)
        cm.on_system_start("system.start", {})
        assert target.exists()

    def test_system_stop_calls_bus_stop(self):
        cm.on_system_stop("system.stop", {})
        cm.bus.stop.assert_called_once()
