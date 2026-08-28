"""In-process adapters used by the embedded Qt composition."""

from __future__ import annotations

from collections.abc import Callable

from backend.contracts.models import CapabilityState, ProjectionState


class EmbeddedProjectionAdapter:
    def __init__(self, bus_client):
        self._bus = bus_client
        self._callbacks: list[Callable[[ProjectionState], None]] = []
        self._state = ProjectionState(capability=CapabilityState(True))
        self._bus.subscribe("video.stream_start", self._on_started)
        self._bus.subscribe("video.stream_stop", self._on_stopped)
        self._bus.subscribe("media.audio.mic_control", self._on_mic_control)

    def subscribe_state(self, callback: Callable[[ProjectionState], None]):
        self._callbacks.append(callback)
        callback(self._state)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe

    def request_focus(self, mode: str) -> None:
        self._bus.publish("media.video.request_focus", {"mode": mode, "sender": "qt6_gui"})

    def send_touch(self, event: dict) -> None:
        self._bus.publish("input.event", event)

    def send_microphone(self, pcm_data: bytes) -> None:
        self._bus.publish("media.audio.mic_bytes", {"payload": pcm_data})

    def _on_started(self, topic: str, payload: dict) -> None:
        self._set_state(connected=True)

    def _on_stopped(self, topic: str, payload: dict) -> None:
        self._set_state(connected=False)

    def _on_mic_control(self, topic: str, payload: dict) -> None:
        self._set_state(mic_enabled=bool(payload.get("enabled", False)))

    def _set_state(self, **changes) -> None:
        self._state = ProjectionState(
            connected=changes.get("connected", self._state.connected),
            mic_enabled=changes.get("mic_enabled", self._state.mic_enabled),
            capability=self._state.capability,
        )
        for callback in tuple(self._callbacks):
            callback(self._state)
