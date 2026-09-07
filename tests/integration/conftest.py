# tests/integration/conftest.py
import pytest
from pathlib import Path
import zmq
import zmq.asyncio
from tests.integration.harness.environment import IntegrationEnvironment

# Prevent pyzmq Context.__del__ from blocking forever during test GC teardown
try:
    zmq.Context.__del__ = lambda self: None
    zmq.asyncio.Context.__del__ = lambda self: None
except Exception:
    pass

@pytest.fixture
def integration_env(tmp_path: Path):
    with IntegrationEnvironment(tmp_path) as env:
        yield env
