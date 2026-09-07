import pytest
from shared.ipc_utils import get_bus_address, PUB_PORT, SUB_PORT


def test_ipc_utils_linux_endpoints(monkeypatch):
    monkeypatch.setattr("shared.ipc_utils.IS_WINDOWS", False)
    assert get_bus_address(kind="pub") == "ipc:///tmp/nemobus_v2.sub"
    assert get_bus_address(kind="sub") == "ipc:///tmp/nemobus_v2.pub"


def test_ipc_utils_windows_endpoints(monkeypatch):
    monkeypatch.setattr("shared.ipc_utils.IS_WINDOWS", True)
    assert get_bus_address(kind="pub") == f"tcp://127.0.0.1:{PUB_PORT}"
    assert get_bus_address(kind="sub") == f"tcp://127.0.0.1:{SUB_PORT}"


def test_ipc_utils_default_arguments(monkeypatch):
    monkeypatch.setattr("shared.ipc_utils.IS_WINDOWS", False)
    assert get_bus_address() == "ipc:///tmp/nemobus_v2.sub"
