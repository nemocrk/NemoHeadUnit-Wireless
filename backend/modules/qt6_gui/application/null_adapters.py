"""Standalone providers that make unavailable capabilities explicit and harmless."""

from __future__ import annotations

from collections.abc import Callable

from backend.contracts.models import AudioState, ConnectivityState, ProjectionState


class _Observable:
    def __init__(self, state):
        self._state = state
        self._callbacks: list[Callable] = []

    def subscribe(self, callback: Callable):
        self._callbacks.append(callback)
        callback(self._state)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe


class NullProjectionAdapter(_Observable):
    def __init__(self):
        super().__init__(ProjectionState())

    subscribe_state = _Observable.subscribe

    def request_focus(self, mode: str) -> None:
        return None

    def send_touch(self, event: dict) -> None:
        return None

    def send_microphone(self, pcm_data: bytes) -> None:
        return None


class NullConnectivityAdapter(_Observable):
    def __init__(self):
        super().__init__(ConnectivityState())

    subscribe_state = _Observable.subscribe

    def request_scan(self) -> None:
        return None


class LocalSettingsAdapter:
    def __init__(self, initial: dict | None = None):
        self._settings = initial or {"qt6_gui": {"config": {"fullscreen": False, "theme": "dark", "enable_mic": True}, "schema": {}}}

    def list_settings(self) -> dict:
        return self._settings

    def save_settings(self, module: str, values: dict) -> None:
        entry = self._settings.setdefault(module, {"config": {}, "schema": {}})
        entry.setdefault("config", {}).update(values)


class NullAudioControlAdapter(_Observable):
    def __init__(self):
        super().__init__(AudioState())

    subscribe_state = _Observable.subscribe

    def request_volume_action(self, action: str) -> None:
        return None


class NullDiagnosticsAdapter:
    def subscribe_logs(self, callback: Callable[[str], None]):
        callback("Standalone mode: no diagnostics provider is attached.")
        return lambda: None
