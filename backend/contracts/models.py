"""Value objects shared across UI controllers and provider adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CapabilityState:
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class ProjectionState:
    connected: bool = False
    mic_enabled: bool = False
    capability: CapabilityState = field(default_factory=lambda: CapabilityState(False, "Projection provider unavailable"))


@dataclass(frozen=True)
class AudioState:
    volume: int = 80
    muted: bool = False
    capability: CapabilityState = field(default_factory=lambda: CapabilityState(False, "Audio control provider unavailable"))


@dataclass(frozen=True)
class ConnectivityState:
    discovering: bool = False
    connected: bool = False
    stage_label: str = "Standalone"
    paired_devices: tuple[dict[str, Any], ...] = ()
    discovered_devices: tuple[dict[str, Any], ...] = ()
    capability: CapabilityState = field(default_factory=lambda: CapabilityState(False, "Connectivity provider unavailable"))
