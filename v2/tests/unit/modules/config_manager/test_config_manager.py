"""
Unit tests for config_manager/main.py

Strategy:
  Il modulo usa BusClient, yaml, e pathlib. Viene importato una volta
  sola a livello di file con:
    - patch("shared.bus_client.BusClient") per iniettare mock_bus
    - patch("shared.logger.get_logger") per silenziare il logger
    - patch("yaml.safe_load") / patch("yaml.safe_dump") nei singoli test
      dove serve controllare I/O file senza toccare il filesystem

  La fixture `cm` reloada il modulo, resetta _schemas e restituisce
  (mod, mock_bus). Il mock_bus cattura tutte le chiamate a publish.

  I/O file viene controllato via:
    - patch.object(mod, "_load_config") / patch.object(mod, "_save_config")
    per i test degli handler di alto livello
    - patch("pathlib.Path.open") + patch("yaml.safe_load") / "yaml.safe_dump"
    per i test diretti di _load_config/_save_config
    (_load_config usa Path.open(), non builtins.open())

Covers:
  Section 1  — _config_path(): formato path corretto
  Section 2  — _load_config(): file non esiste → {}, parse ok, yaml non dict → {},
               eccezione → {}
  Section 3  — _save_config(): crea dir, scrive yaml, eccezione → False,
               successo → True
  Section 4  — _defaults_from_schema(): schema vuoto → {}, ConfigFieldSchema con default,
               ConfigFieldSchema senza default escluso, ConfigFieldList con default,
               ConfigFieldList vuoto escluso, nodo strutturato escluso
  Section 5  — _schema_dict_for_response(): nessuno schema → None, schema presente → dict
  Section 6  — on_system_readytostart(): pubblica system.module_ready con name+priority
  Section 7  — on_system_start(): priority errata ignorata, priority ok → pubblica system.ready,
               crea CONFIG_DIR, chiama bus.publish con name+priority
  Section 8  — on_system_stop(): chiama bus.stop()
  Section 9  — on_config_get(): modulo mancante ignorato, modulo esistente ritorna config,
               nessun file + defaults= → seeded + risposta, nessun file senza defaults → config={},
               schema= nel payload → memorizzato in _schemas,
               schema presente → echoed nella risposta, requester echoed,
               nessun file + schema con defaults → schema-first seeding,
               _save_config fallisce → config vuota nella risposta
  Section 10 — on_config_set(): modulo o chiave mancante ignorati,
               nessuno schema → salva e pubblica config.changed,
               schema presente + valore valido → coerced + salvato,
               schema presente + valore invalido → pubblica config.error + non salva,
               campo strutturato (non ConfigFieldSchema) → salvato senza validazione,
               _save_config fallisce → no config.changed
"""

from __future__ import annotations

import sys
import types
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch, call, mock_open
import pytest

# ---------------------------------------------------------------------------
# Import module under test
# ---------------------------------------------------------------------------


# _this_module = __name__
# for _k in list(sys.modules.keys()):
#     if _k != _this_module and "config_manager" in _k:
#         del sys.modules[_k]

_mock_bus_instance = MagicMock()
_mock_bus_class    = MagicMock(return_value=_mock_bus_instance)

with patch("shared.bus_client.BusClient", _mock_bus_class), \
     patch("shared.logger.get_logger", return_value=MagicMock()):
    import config_manager.main as _cm_mod
    importlib.reload(_cm_mod)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def cm():
    """
    Reload the module before each test to guarantee a clean state:
      - _schemas is empty
      - bus mock call history cleared
    """
    with patch("shared.bus_client.BusClient", _mock_bus_class), \
         patch("shared.logger.get_logger", return_value=MagicMock()):
        importlib.reload(_cm_mod)
    _cm_mod._schemas.clear()
    _mock_bus_instance.reset_mock()
    yield _cm_mod, _mock_bus_instance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _published_topics(mock_bus) -> list[str]:
    return [c.args[0] for c in mock_bus.publish.call_args_list]


def _published_payload(mock_bus, topic: str) -> dict:
    for c in mock_bus.publish.call_args_list:
        if c.args[0] == topic:
            return c.args[1]
    return {}


def _make_scalar_field(type_: str = "string", default=None):
    """Return a real ConfigFieldSchema so isinstance checks exercise production code."""
    from shared.config_schema import ConfigFieldSchema
    return ConfigFieldSchema(type=type_, default=default)


def _make_list_field(default=None):
    from shared.config_schema import ConfigFieldList, ConfigFieldSchema
    return ConfigFieldList(
        item_schema=ConfigFieldSchema(type="string", default=""),
        default=default or [],
    )


# ===========================================================================
# Section 1 — _config_path()
# ===========================================================================

class TestConfigPath:

    @pytest.mark.unit
    def test_returns_path_object(self, cm):
        mod, _ = cm
        result = mod._config_path("bluetooth")
        assert isinstance(result, Path)

    @pytest.mark.unit
    def test_ends_with_module_yaml(self, cm):
        mod, _ = cm
        result = mod._config_path("bluetooth")
        assert result.name == "bluetooth.yaml"

    @pytest.mark.unit
    def test_parent_is_config_dir(self, cm):
        mod, _ = cm
        result = mod._config_path("bluetooth")
        assert result.parent == mod.CONFIG_DIR


# ===========================================================================
# Section 2 — _load_config()
# ===========================================================================

class TestLoadConfig:

    @pytest.mark.unit
    def test_returns_empty_when_file_missing(self, cm):
        mod, _ = cm
        with patch("pathlib.Path.exists", return_value=False):
            result = mod._load_config("nomodule")
        assert result == {}

    @pytest.mark.unit
    def test_returns_dict_when_file_exists(self, cm):
        mod, _ = cm
        data = {"pin": "1234", "enabled": True}
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.open", mock_open(read_data="")), \
             patch("yaml.safe_load", return_value=data):
            result = mod._load_config("bluetooth")
        assert result == data

    @pytest.mark.unit
    def test_returns_empty_when_yaml_not_dict(self, cm):
        mod, _ = cm
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.open", mock_open(read_data="")), \
             patch("yaml.safe_load", return_value=["not", "a", "dict"]):
            result = mod._load_config("bluetooth")
        assert result == {}

    @pytest.mark.unit
    def test_returns_empty_on_exception(self, cm):
        mod, _ = cm
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.open", side_effect=OSError("permission denied")):
            result = mod._load_config("bluetooth")
        assert result == {}

    @pytest.mark.unit
    def test_returns_empty_when_yaml_is_none(self, cm):
        mod, _ = cm
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.open", mock_open(read_data="")), \
             patch("yaml.safe_load", return_value=None):
            result = mod._load_config("bluetooth")
        assert result == {}


# ===========================================================================
# Section 3 — _save_config()
# ===========================================================================

class TestSaveConfig:

    @pytest.mark.unit
    def test_returns_true_on_success(self, cm):
        mod, _ = cm
        with patch("pathlib.Path.mkdir"), \
             patch("pathlib.Path.open", mock_open()), \
             patch("yaml.safe_dump"):
            result = mod._save_config("bluetooth", {"pin": "1234"})
        assert result is True

    @pytest.mark.unit
    def test_creates_config_dir(self, cm):
        mod, _ = cm
        with patch("pathlib.Path.mkdir") as mock_mkdir, \
             patch("pathlib.Path.open", mock_open()), \
             patch("yaml.safe_dump"):
            mod._save_config("bluetooth", {})
        mock_mkdir.assert_called()

    @pytest.mark.unit
    def test_calls_yaml_safe_dump(self, cm):
        mod, _ = cm
        data = {"pin": "1234"}
        with patch("pathlib.Path.mkdir"), \
             patch("pathlib.Path.open", mock_open()), \
             patch("yaml.safe_dump") as mock_dump:
            mod._save_config("bluetooth", data)
        assert mock_dump.called

    @pytest.mark.unit
    def test_returns_false_on_exception(self, cm):
        mod, _ = cm
        with patch("pathlib.Path.mkdir"), \
             patch("pathlib.Path.open", side_effect=OSError("disk full")):
            result = mod._save_config("bluetooth", {})
        assert result is False


# ===========================================================================
# Section 4 — _defaults_from_schema()
# ===========================================================================

class TestDefaultsFromSchema:

    @pytest.mark.unit
    def test_empty_schema_returns_empty(self, cm):
        mod, _ = cm
        result = mod._defaults_from_schema("nomodule")
        assert result == {}

    @pytest.mark.unit
    def test_scalar_field_with_default_included(self, cm):
        mod, _ = cm
        mod._schemas["bt"] = {"pin": _make_scalar_field(default="0000")}
        result = mod._defaults_from_schema("bt")
        assert result["pin"] == "0000"

    @pytest.mark.unit
    def test_scalar_field_without_default_excluded(self, cm):
        mod, _ = cm
        mod._schemas["bt"] = {"pin": _make_scalar_field(default=None)}
        result = mod._defaults_from_schema("bt")
        assert "pin" not in result

    @pytest.mark.unit
    def test_list_field_with_default_included(self, cm):
        mod, _ = cm
        mod._schemas["svc"] = {"channels": _make_list_field(default=[{"id": 1}])}
        result = mod._defaults_from_schema("svc")
        assert result["channels"] == [{"id": 1}]

    @pytest.mark.unit
    def test_list_field_empty_default_excluded(self, cm):
        mod, _ = cm
        mod._schemas["svc"] = {"channels": _make_list_field(default=[])}
        result = mod._defaults_from_schema("svc")
        assert "channels" not in result

    @pytest.mark.unit
    def test_structured_node_excluded(self, cm):
        mod, _ = cm
        # A MagicMock that is neither ConfigFieldSchema nor ConfigFieldList
        mod._schemas["bt"] = {"meta": MagicMock(spec=object)}
        result = mod._defaults_from_schema("bt")
        assert result == {}


# ===========================================================================
# Section 5 — _schema_dict_for_response()
# ===========================================================================

class TestSchemaDictForResponse:

    @pytest.mark.unit
    def test_returns_none_when_no_schema(self, cm):
        mod, _ = cm
        result = mod._schema_dict_for_response("nomodule")
        assert result is None

    @pytest.mark.unit
    def test_returns_dict_when_schema_present(self, cm):
        mod, _ = cm
        mod._schemas["bt"] = {"pin": _make_scalar_field()}
        with patch("config_manager.main.schema_to_dict", return_value={"pin": {"type": "string"}}):
            result = mod._schema_dict_for_response("bt")
        assert isinstance(result, dict)


# ===========================================================================
# Section 6 — on_system_readytostart()
# ===========================================================================

class TestOnSystemReadyToStart:

    @pytest.mark.unit
    def test_publishes_module_ready(self, cm):
        mod, mock_bus = cm
        mod.on_system_readytostart()
        assert "system.module_ready" in _published_topics(mock_bus)

    @pytest.mark.unit
    def test_module_ready_has_name(self, cm):
        mod, mock_bus = cm
        mod.on_system_readytostart()
        payload = _published_payload(mock_bus, "system.module_ready")
        assert payload["name"] == mod.MODULE_NAME

    @pytest.mark.unit
    def test_module_ready_has_priority(self, cm):
        mod, mock_bus = cm
        mod.on_system_readytostart()
        payload = _published_payload(mock_bus, "system.module_ready")
        assert payload["priority"] == mod.PRIORITY


# ===========================================================================
# Section 7 — on_system_start()
# ===========================================================================

class TestOnSystemStart:

    @pytest.mark.unit
    def test_ignores_wrong_priority(self, cm):
        mod, mock_bus = cm
        mod.on_system_start("system.start", {"priority": 99})
        assert "system.ready" not in _published_topics(mock_bus)

    @pytest.mark.unit
    def test_publishes_system_ready_on_correct_priority(self, cm):
        mod, mock_bus = cm
        with patch("pathlib.Path.mkdir"):
            mod.on_system_start("system.start", {"priority": mod.PRIORITY})
        assert "system.ready" in _published_topics(mock_bus)

    @pytest.mark.unit
    def test_system_ready_has_name(self, cm):
        mod, mock_bus = cm
        with patch("pathlib.Path.mkdir"):
            mod.on_system_start("system.start", {"priority": mod.PRIORITY})
        payload = _published_payload(mock_bus, "system.ready")
        assert payload["name"] == mod.MODULE_NAME

    @pytest.mark.unit
    def test_system_ready_has_priority(self, cm):
        mod, mock_bus = cm
        with patch("pathlib.Path.mkdir"):
            mod.on_system_start("system.start", {"priority": mod.PRIORITY})
        payload = _published_payload(mock_bus, "system.ready")
        assert payload["priority"] == mod.PRIORITY

    @pytest.mark.unit
    def test_creates_config_dir(self, cm):
        mod, mock_bus = cm
        with patch("pathlib.Path.mkdir") as mock_mkdir:
            mod.on_system_start("system.start", {"priority": mod.PRIORITY})
        mock_mkdir.assert_called()


# ===========================================================================
# Section 8 — on_system_stop()
# ===========================================================================

class TestOnSystemStop:

    @pytest.mark.unit
    def test_calls_bus_stop(self, cm):
        mod, mock_bus = cm
        mod.on_system_stop("system.stop", {})
        mock_bus.stop.assert_called_once()


# ===========================================================================
# Section 9 — on_config_get()
# ===========================================================================

class TestOnConfigGet:

    @pytest.mark.unit
    def test_missing_module_field_ignored(self, cm):
        mod, mock_bus = cm
        mod.on_config_get("config.get", {})
        assert "config.response" not in _published_topics(mock_bus)

    @pytest.mark.unit
    def test_existing_config_returned(self, cm):
        mod, mock_bus = cm
        stored = {"pin": "1234"}
        with patch.object(mod, "_load_config", return_value=stored):
            mod.on_config_get("config.get", {"module": "bluetooth"})
        payload = _published_payload(mock_bus, "config.response")
        assert payload["config"] == stored

    @pytest.mark.unit
    def test_requester_echoed(self, cm):
        mod, mock_bus = cm
        with patch.object(mod, "_load_config", return_value={"k": "v"}):
            mod.on_config_get("config.get", {"module": "bt", "requester": "audio_manager"})
        payload = _published_payload(mock_bus, "config.response")
        assert payload["requester"] == "audio_manager"

    @pytest.mark.unit
    def test_no_file_with_defaults_seeds_and_returns(self, cm):
        mod, mock_bus = cm
        defaults = {"pin": "0000", "enabled": True}
        with patch.object(mod, "_load_config", return_value={}), \
             patch.object(mod, "_save_config", return_value=True):
            mod.on_config_get("config.get", {"module": "bt", "defaults": defaults})
        payload = _published_payload(mock_bus, "config.response")
        assert payload["config"] == defaults

    @pytest.mark.unit
    def test_no_file_no_defaults_returns_empty_config(self, cm):
        mod, mock_bus = cm
        with patch.object(mod, "_load_config", return_value={}), \
             patch.object(mod, "_defaults_from_schema", return_value={}):
            mod.on_config_get("config.get", {"module": "bt"})
        payload = _published_payload(mock_bus, "config.response")
        assert payload["config"] == {}

    @pytest.mark.unit
    def test_schema_in_payload_registered(self, cm):
        mod, mock_bus = cm
        raw_schema = {"pin": {"type": "string", "default": "0000"}}
        with patch.object(mod, "_load_config", return_value={"pin": "1234"}), \
             patch("config_manager.main.schema_from_dict", return_value={"pin": _make_scalar_field(default="0000")}):
            mod.on_config_get("config.get", {"module": "bt", "schema": raw_schema})
        assert "bt" in mod._schemas

    @pytest.mark.unit
    def test_schema_echoed_in_response(self, cm):
        mod, mock_bus = cm
        mod._schemas["bt"] = {"pin": _make_scalar_field()}
        with patch.object(mod, "_load_config", return_value={"pin": "1234"}), \
             patch("config_manager.main.schema_to_dict", return_value={"pin": {"type": "string"}}):
            mod.on_config_get("config.get", {"module": "bt"})
        payload = _published_payload(mock_bus, "config.response")
        assert "schema" in payload

    @pytest.mark.unit
    def test_no_schema_not_in_response(self, cm):
        mod, mock_bus = cm
        with patch.object(mod, "_load_config", return_value={"pin": "1234"}):
            mod.on_config_get("config.get", {"module": "bt"})
        payload = _published_payload(mock_bus, "config.response")
        assert "schema" not in payload

    @pytest.mark.unit
    def test_schema_first_seeding_from_schema_defaults(self, cm):
        mod, mock_bus = cm
        schema_defaults = {"pin": "0000", "enabled": True}
        with patch.object(mod, "_load_config", return_value={}), \
             patch.object(mod, "_defaults_from_schema", return_value=schema_defaults), \
             patch.object(mod, "_save_config", return_value=True):
            mod.on_config_get("config.get", {"module": "bt"})
        payload = _published_payload(mock_bus, "config.response")
        assert payload["config"] == schema_defaults

    @pytest.mark.unit
    def test_save_failure_returns_empty_config(self, cm):
        mod, mock_bus = cm
        defaults = {"pin": "0000"}
        with patch.object(mod, "_load_config", return_value={}), \
             patch.object(mod, "_save_config", return_value=False):
            mod.on_config_get("config.get", {"module": "bt", "defaults": defaults})
        payload = _published_payload(mock_bus, "config.response")
        assert payload["config"] == {}

    @pytest.mark.unit
    def test_publishes_config_response(self, cm):
        mod, mock_bus = cm
        with patch.object(mod, "_load_config", return_value={"k": "v"}):
            mod.on_config_get("config.get", {"module": "bt"})
        assert "config.response" in _published_topics(mock_bus)


# ===========================================================================
# Section 10 — on_config_set()
# ===========================================================================

class TestOnConfigSet:

    @pytest.mark.unit
    def test_missing_module_ignored(self, cm):
        mod, mock_bus = cm
        mod.on_config_set("config.set", {"key": "pin", "value": "1234"})
        assert "config.changed" not in _published_topics(mock_bus)

    @pytest.mark.unit
    def test_missing_key_ignored(self, cm):
        mod, mock_bus = cm
        mod.on_config_set("config.set", {"module": "bt", "value": "1234"})
        assert "config.changed" not in _published_topics(mock_bus)

    @pytest.mark.unit
    def test_no_schema_saves_and_publishes_changed(self, cm):
        mod, mock_bus = cm
        with patch.object(mod, "_load_config", return_value={}), \
             patch.object(mod, "_save_config", return_value=True):
            mod.on_config_set("config.set", {"module": "bt", "key": "pin", "value": "1234"})
        assert "config.changed" in _published_topics(mock_bus)

    @pytest.mark.unit
    def test_config_changed_payload(self, cm):
        mod, mock_bus = cm
        with patch.object(mod, "_load_config", return_value={}), \
             patch.object(mod, "_save_config", return_value=True):
            mod.on_config_set("config.set", {"module": "bt", "key": "pin", "value": "9999"})
        payload = _published_payload(mock_bus, "config.changed")
        assert payload == {"module": "bt", "key": "pin", "value": "9999"}

    @pytest.mark.unit
    def test_valid_value_coerced_by_schema(self, cm):
        mod, mock_bus = cm
        from shared.config_schema import ConfigFieldSchema
        field = ConfigFieldSchema(type="int", default=0, min=0, max=100)
        mod._schemas["bt"] = {"volume": field}
        with patch("config_manager.main.validate_value", return_value=80) as mock_validate, \
             patch.object(mod, "_load_config", return_value={}), \
             patch.object(mod, "_save_config", return_value=True):
            mod.on_config_set("config.set", {"module": "bt", "key": "volume", "value": "80"})
        mock_validate.assert_called_once()
        payload = _published_payload(mock_bus, "config.changed")
        assert payload["value"] == 80

    @pytest.mark.unit
    def test_invalid_value_publishes_config_error(self, cm):
        mod, mock_bus = cm
        from shared.config_schema import ConfigFieldSchema
        field = ConfigFieldSchema(type="int", default=0, min=0, max=100)
        mod._schemas["bt"] = {"volume": field}
        with patch("config_manager.main.validate_value", side_effect=ValueError("out of range")), \
             patch.object(mod, "_load_config", return_value={}), \
             patch.object(mod, "_save_config") as mock_save:
            mod.on_config_set("config.set", {"module": "bt", "key": "volume", "value": "999"})
        assert "config.error" in _published_topics(mock_bus)
        assert "config.changed" not in _published_topics(mock_bus)
        mock_save.assert_not_called()

    @pytest.mark.unit
    def test_config_error_payload_has_reason(self, cm):
        mod, mock_bus = cm
        from shared.config_schema import ConfigFieldSchema
        field = ConfigFieldSchema(type="int", default=0, min=0, max=100)
        mod._schemas["bt"] = {"volume": field}
        with patch("config_manager.main.validate_value", side_effect=ValueError("out of range")), \
             patch.object(mod, "_load_config", return_value={}):
            mod.on_config_set("config.set", {"module": "bt", "key": "volume", "value": "999"})
        payload = _published_payload(mock_bus, "config.error")
        assert "reason" in payload
        assert "out of range" in payload["reason"]

    @pytest.mark.unit
    def test_structured_field_saved_without_validation(self, cm):
        mod, mock_bus = cm
        # A non-ConfigFieldSchema field (e.g. ConfigFieldList)
        from shared.config_schema import ConfigFieldList, ConfigFieldSchema
        field = ConfigFieldList(item_schema=ConfigFieldSchema(type="int", default=0))
        mod._schemas["svc"] = {"channels": field}
        with patch("config_manager.main.validate_value") as mock_validate, \
             patch.object(mod, "_load_config", return_value={}), \
             patch.object(mod, "_save_config", return_value=True):
            mod.on_config_set("config.set", {"module": "svc", "key": "channels", "value": [{"id": 1}]})
        mock_validate.assert_not_called()
        assert "config.changed" in _published_topics(mock_bus)

    @pytest.mark.unit
    def test_save_failure_no_config_changed(self, cm):
        mod, mock_bus = cm
        with patch.object(mod, "_load_config", return_value={}), \
             patch.object(mod, "_save_config", return_value=False):
            mod.on_config_set("config.set", {"module": "bt", "key": "pin", "value": "1234"})
        assert "config.changed" not in _published_topics(mock_bus)
