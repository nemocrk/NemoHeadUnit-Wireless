"""
NemoHeadUnit-Wireless v2 — ConfigClient

Convenience helper that any module can use to interact with the
config_manager module without hand-crafting bus messages.

Usage inside a module:

    from shared.config_client import ConfigClient

    cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)
    cfg.register()                          # before bus.start()
    cfg.get(defaults=_DEFAULTS)             # async → on_config_loaded(config)
    cfg.set("pin", "1234")                  # async → on_config_changed(key, value)

    cfg.on_config_loaded  = lambda config: ...
    cfg.on_config_changed = lambda key, value: ...

The helper subscribes to config.response and config.changed and filters
by module_name so multiple modules can coexist safely on the same bus.

config.get is published with a "requester" field set to module_name.
config_manager echoes this field back in config.response, allowing UI
modules (e.g. config_ui) to ignore responses not directed at them.

If "defaults" is passed to get(), config_manager will persist them on
first boot (when no YAML exists yet) and return them in the same
config.response, so config_ui sees the keys immediately.
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
        self.on_config_loaded:  Callable[[dict], None]        | None = None
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

    def get(self, defaults: dict | None = None) -> None:
        """
        Request the full config for this module.
        The result is delivered asynchronously via on_config_loaded.

        Parameters
        ----------
        defaults : optional dict of default key/value pairs.
            If provided and no YAML exists yet for this module,
            config_manager will persist the defaults atomically and
            return them in the same config.response — so observers
            (e.g. config_ui) see the keys on first boot without a
            second round-trip.
        """
        payload: dict = {
            "module":    self._module_name,
            "requester": self._module_name,
        }
        if defaults is not None:
            payload["defaults"] = defaults
        self._bus.publish("config.get", payload)

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
        # Filter: only process responses directed at this module
        # (requester field matches module_name, set by get())
        if payload.get("requester") != self._module_name:
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
