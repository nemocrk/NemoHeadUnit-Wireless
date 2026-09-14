import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from modules.config_manager.main import ConfigManagerModule, get_user_config_dir
from shared.config_schema import field_int, field_string, schema_to_dict

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_config_manager(tmp_path):
    with patch("shared.base_module.BusClient"):
        with patch("modules.config_manager.main.get_user_config_dir", return_value=tmp_path):
            mgr = ConfigManagerModule()
            yield mgr


def test_get_user_config_dir_env_override(tmp_path, monkeypatch):
    custom_dir = tmp_path / "custom_config"
    monkeypatch.setenv("NEMO_CONFIG_DIR", str(custom_dir))
    res = get_user_config_dir()
    assert res == custom_dir
    assert custom_dir.exists()


def test_config_manager_yaml_load_and_save(mock_config_manager, tmp_path):
    assert mock_config_manager._load_config("nonexistent") == {}

    data = {"volume": 85, "theme": "dark"}
    saved = mock_config_manager._save_config("audio", data)
    assert saved is True

    loaded = mock_config_manager._load_config("audio")
    assert loaded == data


def test_config_manager_on_config_get_and_set(mock_config_manager):
    mock_config_manager.publish = MagicMock()
    schema = {"level": field_int(10, min=0, max=100)}

    # 1. on_config_get: registers schema, responds with defaults
    mock_config_manager.on_config_get("config.get", {
        "module": "display",
        "defaults": {"level": 50},
        "schema": schema_to_dict(schema),
    })

    assert "display" in mock_config_manager.schemas
    mock_config_manager.publish.assert_any_call("config.response", {
        "module": "display",
        "config": {"level": 50},
        "requester": "",
        "schema": schema_to_dict(schema),
    })

    # 2. on_config_set: updates valid value and publishes updated
    mock_config_manager.on_config_set("config.set", {
        "module": "display",
        "key": "level",
        "value": 75,
    })

    mock_config_manager.publish.assert_called_with("config.updated.display", {
        "module": "display",
        "config": {"level": 75},
    })


def test_config_manager_corrupt_yaml(mock_config_manager, tmp_path):
    corrupt_file = mock_config_manager._config_path("corrupt_mod")
    corrupt_file.write_text("invalid: [yaml: {unclosed", encoding="utf-8")
    loaded = mock_config_manager._load_config("corrupt_mod")
    assert loaded == {}


@pytest.mark.asyncio
async def test_config_manager_rest_api(mock_config_manager):
    import json

    # Setup test schema and config
    mock_config_manager.schemas["test_mod"] = {"name": field_string("nemo")}
    mock_config_manager._save_config("test_mod", {"name": "nemo"})

    # GET /all
    req_all = MagicMock()
    resp_all = await mock_config_manager.handle_get_all(req_all)
    assert resp_all.status == 200
    data_all = json.loads(resp_all.text)
    assert "test_mod" in data_all
    assert data_all["test_mod"]["config"]["name"] == "nemo"

    # GET /test_mod
    req_mod = MagicMock()
    req_mod.match_info = {"module": "test_mod"}
    resp_mod = await mock_config_manager.handle_get_module(req_mod)
    assert resp_mod.status == 200
    data_mod = json.loads(resp_mod.text)
    assert data_mod["module"] == "test_mod"
    assert data_mod["config"]["name"] == "nemo"

    # POST /test_mod valid update
    req_set = MagicMock()
    req_set.match_info = {"module": "test_mod"}
    req_set.json = AsyncMock(return_value={"name": "new_nemo"})
    mock_config_manager.publish = MagicMock()
    resp_set = await mock_config_manager.handle_set_module(req_set)
    assert resp_set.status == 200
    data_set = json.loads(resp_set.text)
    assert data_set["status"] == "ok"
    assert data_set["config"]["name"] == "new_nemo"
    assert mock_config_manager._load_config("test_mod")["name"] == "new_nemo"
    mock_config_manager.publish.assert_called_with(
        "config.updated.test_mod",
        {"module": "test_mod", "config": {"name": "new_nemo"}},
    )


@pytest.mark.asyncio
async def test_config_manager_rest_api_validation_error(mock_config_manager):
    import json

    mock_config_manager.schemas["test_mod"] = {"level": field_int(10, min=0, max=100)}
    mock_config_manager._save_config("test_mod", {"level": 10})

    # 1. Invalid schema value (out of bounds)
    req_invalid = MagicMock()
    req_invalid.match_info = {"module": "test_mod"}
    req_invalid.json = AsyncMock(return_value={"level": 200})
    resp_invalid = await mock_config_manager.handle_set_module(req_invalid)
    assert resp_invalid.status == 400
    data_err = json.loads(resp_invalid.text)
    assert "error" in data_err
    assert "level" in data_err["details"]

    # 2. Invalid JSON payload
    req_bad_json = MagicMock()
    req_bad_json.match_info = {"module": "test_mod"}
    req_bad_json.json = AsyncMock(side_effect=ValueError("bad json"))
    resp_bad = await mock_config_manager.handle_set_module(req_bad_json)
    assert resp_bad.status == 400
    assert "Invalid JSON" in json.loads(resp_bad.text)["error"]
