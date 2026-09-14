# tests/integration/test_harness_env.py
import pytest
import os
from pathlib import Path

pytestmark = pytest.mark.integration

def test_integration_environment_sandbox(tmp_path):
    from tests.integration.harness.environment import IntegrationEnvironment
    
    original_env = os.environ.get("NEMO_CONFIG_DIR")
    with IntegrationEnvironment(tmp_path) as env:
        assert os.environ["NEMO_CONFIG_DIR"] == str(env.config_dir)
        assert env.config_dir.exists()
        assert env.config_file.exists()
        assert "public_port" in env.config_data
        assert env.config_data["public_port"] == 0
    assert os.environ.get("NEMO_CONFIG_DIR") == original_env
