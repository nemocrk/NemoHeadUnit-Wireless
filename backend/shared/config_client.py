"""
Web Browser Head Unit — ConfigClient

Helper for modules to fetch, query, and subscribe to configuration updates from config_manager.
Includes graceful fallback to local default configuration if config_manager is absent.
"""

from typing import Any, Callable, Optional
from shared.logger import get_logger
from shared.config_schema import schema_to_dict


class ConfigClient:
    def __init__(self, module_name: str, bus_client, default_config: Optional[dict[str, Any]] = None, schema: Optional[dict[str, Any]] = None):
        self.module_name = module_name
        self.bus = bus_client
        self.log = get_logger(f"{module_name}.config")
        self.default_config: dict[str, Any] = default_config or {}
        self.schema: Optional[dict[str, Any]] = schema
        self.config_data: dict[str, Any] = dict(self.default_config)
        self.has_remote_config: bool = False
        self._update_callbacks: list[Callable[[dict[str, Any]], None]] = []

    def set_default_config(self, default_config: dict[str, Any], schema: Optional[dict[str, Any]] = None) -> None:
        """Sets or updates local fallback configuration and schema."""
        self.default_config = default_config
        self.schema = schema
        if not self.has_remote_config:
            self.config_data = dict(self.default_config)

    def fetch_config(self) -> dict[str, Any]:
        """
        Requests configuration for this module over ZMQ bus, passing defaults and schema.
        If config_manager is present, it will respond with config.response or config.updated.
        Otherwise, module operates safely on default_config.
        """
        self.log.info(f"Requesting config for '{self.module_name}'...")
        payload = {
            "module": self.module_name,
            "defaults": self.default_config,
        }
        if self.schema:
            payload["schema"] = schema_to_dict(self.schema)

        self.bus.publish("config.get", payload)
        return self.config_data

    def on_update(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Registers a callback for runtime configuration updates."""
        self._update_callbacks.append(callback)

    def handle_config_response(self, topic: str, payload: dict[str, Any]) -> None:
        """Handles config response or update event from config_manager."""
        target_module = payload.get("module")
        if target_module and target_module != self.module_name:
            return

        new_config = payload.get("config", {})
        if new_config:
            self.has_remote_config = True
            merged_config = dict(self.default_config)
            merged_config.update(new_config)
            self.log.info(f"Remote config received for '{self.module_name}': {merged_config}")
            self.config_data = merged_config

            for cb in self._update_callbacks:
                try:
                    cb(self.config_data)
                except Exception as e:
                    self.log.error(f"Error in config update callback: {e}")

    def subscribe_updates(self) -> None:
        """Subscribes to ZMQ configuration topics."""
        self.bus.subscribe(f"config.updated.{self.module_name}", self.handle_config_response)
        self.bus.subscribe("config.response", self.handle_config_response)
