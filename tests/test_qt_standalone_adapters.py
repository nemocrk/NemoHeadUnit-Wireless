import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
for path in (str(REPO_ROOT), str(BACKEND_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from backend.modules.qt6_gui.application.null_adapters import (
    LocalSettingsAdapter,
    NullAudioControlAdapter,
    NullConnectivityAdapter,
    NullProjectionAdapter,
)


def test_standalone_adapters_report_unavailable_capabilities_without_transport():
    projection_states = []
    connectivity_states = []
    audio_states = []

    NullProjectionAdapter().subscribe_state(projection_states.append)
    NullConnectivityAdapter().subscribe_state(connectivity_states.append)
    NullAudioControlAdapter().subscribe_state(audio_states.append)

    assert not projection_states[0].capability.available
    assert not connectivity_states[0].capability.available
    assert not audio_states[0].capability.available


def test_local_settings_adapter_isolated_from_config_service():
    settings = LocalSettingsAdapter()
    settings.save_settings("qt6_gui", {"theme": "light"})

    assert settings.list_settings()["qt6_gui"]["config"]["theme"] == "light"
