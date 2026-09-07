# tests/integration/conftest.py
import pytest
from pathlib import Path
from tests.integration.harness.environment import IntegrationEnvironment

@pytest.fixture
def integration_env(tmp_path: Path):
    with IntegrationEnvironment(tmp_path) as env:
        yield env
