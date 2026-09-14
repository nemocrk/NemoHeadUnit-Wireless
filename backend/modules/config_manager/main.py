#!/usr/bin/env python3
"""
Web Browser Head Unit — Config Manager Module

Priority 1 Core Functional Module extending `BaseBackendModule`.
Centralized configuration service. Persists per-module settings to YAML files in OS
standard user config directory (`~/.config/NemoHeadUnit-Wireless/` on Linux,
`%APPDATA%\\NemoHeadUnit-Wireless\\` on Windows).

Exposes REST API endpoints at `/api/config`:
  - GET /api/config/all       → list all modules, configs, and schemas
  - GET /api/config/{module}  → get specific module config & schema
  - POST /api/config/{module} → update specific module config keys
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict

import yaml
from aiohttp import web

from shared.base_module import BaseBackendModule, run_module
from shared.config_schema import (
    ConfigFieldList,
    ConfigFieldSchema,
    schema_from_dict,
    schema_to_dict,
    validate_value,
)


def get_user_config_dir() -> Path:
    """Returns cross-platform OS standard user configuration directory."""
    if "NEMO_CONFIG_DIR" in os.environ:
        path = Path(os.environ["NEMO_CONFIG_DIR"])
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        path = base / "NemoHeadUnit-Wireless"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
        path = base / "NemoHeadUnit-Wireless"

    path.mkdir(parents=True, exist_ok=True)
    return path


class ConfigManagerModule(BaseBackendModule):
    def __init__(self):
        super().__init__(
            name="config_manager",
            priority=1,
            path_prefix="/api/config",
        )
        self.config_dir = get_user_config_dir()
        self.schemas: Dict[str, dict] = {}

    def get_default_config(self) -> dict[str, Any]:
        return {
            "autosave": True,
        }

    async def setup(self) -> None:
        """Initialize storage directory, ZMQ subscribers, and REST API routes."""
        self.log.info(f"Config storage directory active at: {self.config_dir}")

        # Subscribe to ZMQ bus configuration requests
        self.subscribe("config.get", self.on_config_get)
        self.subscribe("config.set", self.on_config_set)

        # Register REST API endpoints
        self.add_http_route("GET", "/all", self.handle_get_all)
        self.add_http_route("GET", "/{module}", self.handle_get_module)
        self.add_http_route("POST", "/{module}", self.handle_set_module)

    def _config_path(self, module_name: str) -> Path:
        return self.config_dir / f"{module_name}.yaml"

    def _load_config(self, module_name: str) -> dict:
        path = self._config_path(module_name)
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
                return data if isinstance(data, dict) else {}
        except Exception as exc:
            self.log.error(f"Failed to read config for '{module_name}': {exc}")
            return {}

    def _save_config(self, module_name: str, data: dict) -> bool:
        path = self._config_path(module_name)
        try:
            with path.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(data, fh, default_flow_style=False, allow_unicode=True)
            return True
        except Exception as exc:
            self.log.error(f"Failed to write config for '{module_name}': {exc}")
            return False

    def _defaults_from_schema(self, module_name: str) -> dict:
        schema = self.schemas.get(module_name, {})
        result: dict = {}
        for k, v in schema.items():
            if isinstance(v, ConfigFieldSchema) and v.default is not None:
                result[k] = v.default
            elif isinstance(v, ConfigFieldList) and v.default:
                result[k] = v.default
        return result

    def on_config_get(self, topic: str, payload: dict) -> None:
        module = payload.get("module")
        requester = payload.get("requester", "")
        defaults = payload.get("defaults")
        raw_schema = payload.get("schema")

        if not module:
            self.log.warning("config.get received without 'module' field — ignoring.")
            return

        if isinstance(raw_schema, dict) and raw_schema:
            try:
                self.schemas[module] = schema_from_dict(raw_schema)
                self.log.info(f"Schema registered for '{module}': {list(raw_schema.keys())}")
            except Exception as exc:
                self.log.error(f"Failed to parse schema for '{module}': {exc}")

        config = self._load_config(module)

        if not config:
            if isinstance(defaults, dict) and defaults:
                seed = defaults
            else:
                seed = self._defaults_from_schema(module)

            if seed:
                if self._save_config(module, seed):
                    config = seed
                    self.log.info(f"Seeded initial config for '{module}' ({len(seed)} keys)")
        elif isinstance(defaults, dict) and defaults:
            updated = False
            for k, v in defaults.items():
                if k not in config:
                    config[k] = v
                    updated = True
            if updated:
                self._save_config(module, config)
                self.log.info(f"Updated config for '{module}' with new default keys ({len(config)} total keys)")

        schema_payload = schema_to_dict(self.schemas[module]) if module in self.schemas else None

        response = {
            "module": module,
            "config": config,
            "requester": requester,
        }
        if schema_payload:
            response["schema"] = schema_payload

        # Publish direct response and update event
        self.publish("config.response", response)
        self.publish(f"config.updated.{module}", response)

    def on_config_set(self, topic: str, payload: dict) -> None:
        module = payload.get("module")
        key = payload.get("key")
        value = payload.get("value")

        if not module or key is None:
            self.log.warning(f"config.set missing 'module' or 'key': {payload}")
            return

        schema = self.schemas.get(module)
        if schema and key in schema:
            field_schema = schema[key]
            if isinstance(field_schema, ConfigFieldSchema):
                try:
                    value = validate_value(field_schema, value)
                except ValueError as exc:
                    reason = str(exc)
                    self.log.warning(f"Validation failed for '{module}.{key} = {value!r}': {reason}")
                    self.publish("config.error", {
                        "module": module,
                        "key": key,
                        "value": payload.get("value"),
                        "reason": reason,
                    })
                    return

        data = self._load_config(module)
        data[key] = value

        if self._save_config(module, data):
            self.log.info(f"Updated config '{module}.{key}' = {value!r}")
            self.publish("config.changed", {"module": module, "key": key, "value": value})
            self.publish(f"config.updated.{module}", {"module": module, "config": data})

    async def handle_get_all(self, request: web.Request) -> web.Response:
        """REST API: GET /api/config/all"""
        modules_data = {}
        # Collect modules with registered schemas or active config files
        active_modules = set(self.schemas.keys())
        for path in self.config_dir.glob("*.yaml"):
            active_modules.add(path.stem)

        for mod_name in sorted(active_modules):
            # Skip obsolete standalone modules
            if mod_name in ("bluetooth_manager", "hostapd_helper", "audio_channel", "video_channel", "oaa_control_channel"):
                continue
            config = self._load_config(mod_name)
            schema = schema_to_dict(self.schemas[mod_name]) if mod_name in self.schemas else None
            modules_data[mod_name] = {
                "config": config,
                "schema": schema,
            }
        return web.json_response(modules_data)

    async def handle_get_module(self, request: web.Request) -> web.Response:
        """REST API: GET /api/config/{module}"""
        module_name = request.match_info["module"]
        config = self._load_config(module_name)
        schema = schema_to_dict(self.schemas[module_name]) if module_name in self.schemas else None
        return web.json_response({
            "module": module_name,
            "config": config,
            "schema": schema,
        })

    async def handle_set_module(self, request: web.Request) -> web.Response:
        """REST API: POST /api/config/{module}"""
        module_name = request.match_info["module"]
        try:
            updates = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON payload"}, status=400)

        data = self._load_config(module_name)
        errors = {}
        schema = self.schemas.get(module_name)

        for key, value in updates.items():
            if schema and key in schema:
                field_schema = schema[key]
                if isinstance(field_schema, ConfigFieldSchema):
                    try:
                        value = validate_value(field_schema, value)
                    except ValueError as exc:
                        errors[key] = str(exc)
                        continue
            data[key] = value

        if not errors:
            self._save_config(module_name, data)
            self.publish(f"config.updated.{module_name}", {"module": module_name, "config": data})
            return web.json_response({"status": "ok", "config": data})

        return web.json_response({"error": "Validation failed", "details": errors}, status=400)

    async def run(self) -> None:
        """Main execution loop."""
        self.log.info("ConfigManager loop active...")
        while self._running:
            await asyncio.sleep(1)

    async def teardown(self) -> None:
        self.log.info("ConfigManager teardown complete.")


if __name__ == "__main__":
    run_module(ConfigManagerModule)
