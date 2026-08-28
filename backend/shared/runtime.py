"""Process-local runtime selection used only by explicitly embedded compositions."""

from __future__ import annotations

from typing import Optional

from shared.inprocess_bus import InProcessBus

_inprocess_bus: Optional[InProcessBus] = None


def configure_inprocess_runtime(bus: InProcessBus) -> None:
    global _inprocess_bus
    _inprocess_bus = bus


def get_inprocess_bus() -> Optional[InProcessBus]:
    return _inprocess_bus


def is_inprocess_runtime() -> bool:
    return _inprocess_bus is not None


def clear_inprocess_runtime() -> None:
    global _inprocess_bus
    _inprocess_bus = None
