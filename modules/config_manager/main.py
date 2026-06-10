"""
NemoHeadUnit-Wireless — config_manager module

Centralised configuration service. Persists per-module settings to YAML files
and notifies subscribers when a value changes.

---
Module contract:

  Name        : config_manager
  Priority    : 0  (infrastructure — must be ready before all other modules)
  Subscribes  : system.readytostart
                system.start
                system.stop
                config.get      → {"module": "<name>",
                                    "requester": "<who>" (optional),
                                    "defaults": {<key>: <value>, ...} (optional),
                                    "schema": {<key>: {type, ...}, ...} (optional)}
                config.set      → {"module": "<name>", "key": "<k>", "value": <v>}
  Publishes   : system.module_ready → {name, priority}
                system.ready      → {name, priority}
                config.response   → {"module": "<name>", "config": {<key>: <value>, ...},
                                      "requester": "<who>" (echoed, empty string if absent),
                                      "schema": {<key>: {type, ...}, ...} (echoed if registered)}
                config.changed    → {"module": "<name>", "key": "<k>", "value": <v>}
                config.error      → {"module": "<name>", "key": "<k>", "value": <v>,
                                      "reason": "<human-readable message>"}

  State       : private — YAML files under CONFIG_DIR (one file per module)
                         — in-RAM schema registry (_schemas dict, re-populated at boot)
---

YAML layout  (CONFIG_DIR/<module_name>.yaml):
    pin: "1234"
    enabled: true
    channels:
      - channel_id: 3
        av_channel: {stream_type: VIDEO, ...}
      ...

Rules:
  - The module stores whatever key/value the caller sends, UNLESS a schema
    has been registered for that module and key — in which case the value
    is validated (and coerced) before persisting. On failure, config.error
    is published and the value is NOT persisted.
    Structured schema nodes (ConfigFieldMessage / ConfigFieldList /
    ConfigFieldOneof) are stored as-is without scalar validation; deep
    validation of nested values is the caller's responsibility.
  - config.get returns the full config dict for the requested module.
    If no YAML exists yet AND a "defaults" dict is provided in the payload,
    the defaults are persisted atomically and returned in the same response
    (first-boot seeding, no extra round-trip needed).
    If no YAML exists yet AND no "defaults" are provided but a schema is
    registered, defaults are derived from schema field.default values
    (ConfigFieldSchema scalars AND ConfigFieldList with a non-empty default)
    and seeded the same way (schema-first seeding).
  - If a "schema" dict is provided in config.get, it is stored in RAM and
    echoed verbatim in every subsequent config.response for that module.
    The schema is NOT persisted to disk — modules re-register it on every boot.
  - The optional "requester" field in config.get is echoed verbatim in
    config.response so subscribers can filter responses meant for them.
"""

import sys
from pathlib import Path
import time

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_HERE      = Path(__file__).parent   # modules/config_manager/
_MODULES   = _HERE.parent            # modules/
_REPO_ROOT = _MODULES.parent         # root

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

import yaml  # noqa: E402

from shared.bus_client import BusClient                                          # noqa: E402
from shared.logger import get_logger                                             # noqa: E402
from shared.config_schema import (                                               # noqa: E402
    ConfigFieldList,
    ConfigFieldSchema,
    schema_from_dict,
    schema_to_dict,
    validate_value,
)

# ---------------------------------------------------------------------------
# Module identity & paths
# ---------------------------------------------------------------------------

MODULE_NAME = "config_manager"
PRIORITY    = 0  # infrastructure — first to initialise

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)

CONFIG_DIR = _REPO_ROOT / "config"

# In-RAM schema registry: module_name → {key → AnyFieldSchema}
_schemas: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _config_path(module: str) -> Path:
    return CONFIG_DIR / f"{module}.yaml"


def _load_config(module: str) -> dict:
    path = _config_path(module)
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        log.error(f"Failed to read config for '{module}': {exc}")
        return {}


def _save_config(module: str, data: dict) -> bool:
    path = _config_path(module)
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, default_flow_style=False, allow_unicode=True)
        return True
    except Exception as exc:
        log.error(f"Failed to write config for '{module}': {exc}")
        return False


def _schema_dict_for_response(module: str) -> dict | None:
    schema = _schemas.get(module)
    if not schema:
        return None
    return schema_to_dict(schema)


def _defaults_from_schema(module: str) -> dict:
    schema = _schemas.get(module, {})
    result: dict = {}
    for k, v in schema.items():
        if isinstance(v, ConfigFieldSchema) and v.default is not None:
            result[k] = v.default
        elif isinstance(v, ConfigFieldList) and v.default:
            result[k] = v.default
    return result


# ---------------------------------------------------------------------------
# Bus handlers
# ---------------------------------------------------------------------------

def on_config_get(topic: str, payload: dict):
    module    = payload.get("module")
    requester = payload.get("requester", "")
    defaults  = payload.get("defaults")
    raw_schema = payload.get("schema")

    if not module:
        log.warning("config.get received without 'module' field — ignoring.")
        return

    if isinstance(raw_schema, dict) and raw_schema:
        try:
            _schemas[module] = schema_from_dict(raw_schema)
            log.info(f"Schema registered for '{module}': {list(raw_schema.keys())}")
        except Exception as exc:
            log.error(f"Failed to parse schema for '{module}': {exc}")

    config = _load_config(module)

    if not config:
        if isinstance(defaults, dict) and defaults:
            seed = defaults
            seed_source = f"explicit defaults= ({len(seed)} keys)"
        else:
            seed = _defaults_from_schema(module)
            seed_source = f"schema field.default ({len(seed)} keys)" if seed else None

        if seed:
            if _save_config(module, seed):
                config = seed
                log.info(
                    f"config.get for '{module}' (requester='{requester}'): "
                    f"no YAML found — seeded from {seed_source}."
                )
            else:
                log.warning(
                    f"config.get for '{module}': failed to seed defaults — "
                    "returning empty config."
                )
        else:
            log.info(f"config.get for '{module}' (requester='{requester}') → 0 keys (no defaults)")
    else:
        log.info(f"config.get for '{module}' (requester='{requester}') → {len(config)} keys")

    response: dict = {
        "module":    module,
        "config":    config,
        "requester": requester,
    }
    schema_payload = _schema_dict_for_response(module)
    if schema_payload is not None:
        response["schema"] = schema_payload

    bus.publish("config.response", response)


def on_config_set(topic: str, payload: dict):
    module = payload.get("module")
    key    = payload.get("key")
    value  = payload.get("value")

    if not module or key is None:
        log.warning(f"config.set missing 'module' or 'key': {payload} — ignoring.")
        return

    schema = _schemas.get(module)
    if schema and key in schema:
        field_schema = schema[key]
        if isinstance(field_schema, ConfigFieldSchema):
            try:
                value = validate_value(field_schema, value)
            except ValueError as exc:
                reason = str(exc)
                log.warning(
                    f"config.set validation failed for '{module}'.{key} = {value!r}: {reason}"
                )
                bus.publish("config.error", {
                    "module": module,
                    "key":    key,
                    "value":  payload.get("value"),
                    "reason": reason,
                })
                return
        else:
            log.debug(f"config.set '{module}'.{key}: structured field — skipping scalar validation.")

    data = _load_config(module)
    data[key] = value

    if not _save_config(module, data):
        return

    log.info(f"config.set '{module}'.{key} = {value!r}")
    bus.publish("config.changed", {"module": module, "key": key, "value": value})


# ---------------------------------------------------------------------------
# Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart received — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info(f"system.start priority={PRIORITY} — initialising config_manager")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Config dir ready: {CONFIG_DIR}")

    bus.subscribe("config.get",          on_config_get)
    bus.subscribe("config.set",          on_config_set)

    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    log.info("system.ready published — config_manager online")


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — shutting down config_manager.")
    bus.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    bus.subscribe("system.readytostart", on_system_readytostart)
    bus.subscribe("system.start",        on_system_start)
    bus.subscribe("system.stop",         on_system_stop)

    log.info("config_manager ready — waiting for messages...")
    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()
    try:
        bus_thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
