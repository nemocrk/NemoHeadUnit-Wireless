"""Qt controller that binds passive widgets to transport-neutral application ports."""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from backend.contracts.models import AudioState, ConnectivityState, ProjectionState
from backend.contracts.ports import AudioControlPort, ConnectivityPort, DiagnosticsPort, ProjectionPort, SettingsPort


class QtShellController(QObject):
    _projection_state = pyqtSignal(object)
    _connectivity_state = pyqtSignal(object)
    _audio_state = pyqtSignal(object)
    _log_entry = pyqtSignal(str)

    def __init__(
        self,
        window,
        projection: ProjectionPort,
        connectivity: ConnectivityPort,
        settings: SettingsPort,
        audio: AudioControlPort,
        diagnostics: DiagnosticsPort,
    ):
        super().__init__(window)
        self.window = window
        self._projection = projection
        self._connectivity = connectivity
        self._settings = settings
        self._audio = audio
        self._diagnostics = diagnostics
        self._unsubscribers: list[callable] = []

        self._projection_state.connect(self._apply_projection_state)
        self._connectivity_state.connect(self._apply_connectivity_state)
        self._audio_state.connect(self._apply_audio_state)
        self._log_entry.connect(self.window.logs_drawer.append_log_entry)

        window.projection_focus_requested.connect(self._projection.request_focus)
        window.touch_input_requested.connect(self._projection.send_touch)
        window.microphone_data_requested.connect(self._projection.send_microphone)
        window.bluetooth_scan_requested.connect(self._connectivity.request_scan)
        window.volume_action_requested.connect(self._audio.request_volume_action)
        window.settings_load_requested.connect(self._load_settings)
        window.settings_save_requested.connect(self._save_settings)

    def start(self) -> None:
        self._unsubscribers.extend([
            self._projection.subscribe_state(self._projection_state.emit),
            self._connectivity.subscribe_state(self._connectivity_state.emit),
            self._audio.subscribe_state(self._audio_state.emit),
            self._diagnostics.subscribe_logs(self._log_entry.emit),
        ])

    def close(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    def _load_settings(self) -> None:
        self.window.settings_drawer.set_config_data(self._settings.list_settings())

    def _save_settings(self, module: str, values: dict) -> None:
        self._settings.save_settings(module, values)
        self.window.settings_drawer.set_save_result(True, "Settings saved")
        self._load_settings()

    def _apply_projection_state(self, state: ProjectionState) -> None:
        self.window.set_connected_state(state.connected)

    def _apply_connectivity_state(self, state: ConnectivityState) -> None:
        self.window.bluetooth_drawer.set_connectivity_state(state)

    def _apply_audio_state(self, state: AudioState) -> None:
        self.window.volume_popover.update_volume(state.volume, state.muted)
