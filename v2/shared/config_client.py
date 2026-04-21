"""
NemoHeadUnit-Wireless v2 — ConfigClient

Convenience helper that any module can use to interact with the
config_manager module without hand-crafting bus messages.

Usage inside a module:

    from shared.config_client import ConfigClient

    # Pass your module's BusClient instance and module name.
    cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

    # Register the response/changed handlers BEFORE calling bus.start():
    cfg.register()

    # At any point after the bus is running:
    cfg.get()                          # async — triggers on_config_loaded cb
    cfg.set("pin", "1234")             # async — triggers on_config_changed cb

    # Provide callbacks to react to responses:
    cfg.on_config_loaded  = lambda config: ...   # dict of all keys for this module
    cfg.on_config_changed = lambda key, value: ...  # single key that changed

The helper only listens to config.response / config.changed messages that
belong to its own module_name, so multiple modules can coexist safely.
"""

from __future__ import annotations
from typing import Callable


class ConfigClient:
    """
    Thin wrapper around BusClient that adds config.get / config.set
    request helpers and filters inbound responses by module name.
    """

    def __init__(self, bus, module_name: str):
        """
        Parameters
        ----------
        bus         : BusClient instance belonging to the caller module
        module_name : name of the caller module (used as routing key)
        """
        self._bus         = bus
        self._module_name = module_name

        # Overridable callbacks
        self.on_config_loaded:  Callable[[dict], None]       | None = None
        self.on_config_changed: Callable[[str, object], None] | None = None

    # ------------------------------------------------------------------
    # Registration (call before bus.start)
    # ------------------------------------------------------------------

    def register(self) -> None:
        """Subscribe to config.response and config.changed on the bus."""
        self._bus.subscribe("config.response", self._on_config_response)
        self._bus.subscribe("config.changed",  self._on_config_changed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self) -> None:
        """
        Request the full config for this module.
        The result is delivered asynchronously via on_config_loaded.
        """
        self._bus.publish("config.get", {"module": self._module_name})

    def set(self, key: str, value) -> None:
        """
        Persist a single key/value for this module.
        config_manager will broadcast config.changed after persisting.
        """
        self._bus.publish("config.set", {
            "module": self._module_name,
            "key":    key,
            "value":  value,
        })

    # ------------------------------------------------------------------
    # Internal handlers — filter by module name
    # ------------------------------------------------------------------

    def _on_config_response(self, topic: str, payload: dict) -> None:
        if payload.get("module") != self._module_name:
            return
        if self.on_config_loaded:
            self.on_config_loaded(payload.get("config", {}))

    def _on_config_changed(self, topic: str, payload: dict) -> None:
        if payload.get("module") != self._module_name:
            return
        if self.on_config_changed:
            self.on_config_changed(
                payload.get("key"),
                payload.get("value"),
            )
