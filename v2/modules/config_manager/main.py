"""
NemoHeadUnit-Wireless v2 — config_manager module

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
                                    "defaults": {<key>: <value>, ...} (optional)}
                config.set      → {"module": "<name>", "key": "<k>", "value": <v>}
  Publishes   : system.module_ready → {name, priority}
                system.ready      → {name, priority}
                config.response   → {"module": "<name>", "config": {<key>: <value>, ...},
                                      "requester": "<who>" (echoed, empty string if absent)}
                config.changed    → {"module": "<name>", "key": "<k>", "value": <v>}

  State       : private — YAML files under CONFIG_DIR (one file per module)
---

YAML layout  (CONFIG_DIR/<module_name>.yaml):
    pin: "1234"
    enabled: true
    ...

Rules:
  - The module never enforces a schema; it stores whatever key/value the
    caller sends.
  - config.set only persists and notifies — it does NOT validate the value.
  - config.get returns the full config dict for the requested module.
    If no YAML exists yet AND a "defaults" dict is provided in the payload,
    the defaults are persisted atomically and returned in the same response
    (first-boot seeding, no extra round-trip needed).
  - The optional "requester" field in config.get is echoed verbatim in
    config.response so subscribers can filter responses meant for them.
"""

import sys
from pathlib import Path

_HERE    = Path(__file__).parent    # v2/modules/config_manager/
_MODULES = _HERE.parent             # v2/modules/
_V2      = _MODULES.parent          # v2/

if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

import yaml  # noqa: E402

from shared.bus_client import BusClient  # noqa: E402
from shared.logger import get_logger     # noqa: E402

# ---------------------------------------------------------------------------
# Module identity & paths
# ---------------------------------------------------------------------------

MODULE_NAME = "config_manager"
PRIORITY    = 0  # infrastructure — first to initialise

log = get_logger(MODULE_NAME)

CONFIG_DIR = _V2 / "config"


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


# ---------------------------------------------------------------------------
# Bus handlers
# ---------------------------------------------------------------------------

def on_config_get(topic: str, payload: dict):
    module    = payload.get("module")
    requester = payload.get("requester", "")
    defaults  = payload.get("defaults")

    if not module:
        log.warning("config.get received without 'module' field — ignoring.")
        return

    config = _load_config(module)

    if not config and isinstance(defaults, dict) and defaults:
        if _save_config(module, defaults):
            config = dict(defaults)
            log.info(
                f"config.get for '{module}' (requester='{requester}'): "
                f"no YAML found — seeded {len(config)} defaults."
            )
        else:
            log.warning(
                f"config.get for '{module}': failed to seed defaults — "
                "returning empty config."
            )
    else:
        log.info(
            f"config.get for '{module}' (requester='{requester}') → {len(config)} keys"
        )

    bus.publish("config.response", {
        "module":    module,
        "config":    config,
        "requester": requester,
    })


def on_config_set(topic: str, payload: dict):
    module = payload.get("module")
    key    = payload.get("key")
    value  = payload.get("value")

    if not module or key is None:
        log.warning(f"config.set missing 'module' or 'key': {payload} — ignoring.")
        return

    data = _load_config(module)
    data[key] = value

    if not _save_config(module, data):
        return

    log.info(f"config.set '{module}'.{key} = {value!r}")

    bus.publish("config.changed", {
        "module": module,
        "key":    key,
        "value":  value,
    })


# ---------------------------------------------------------------------------
# Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart(topic: str, payload: dict) -> None:
    log.info(f"system.readytostart received — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return

    log.info(f"system.start priority={PRIORITY} — initialising config_manager")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    log.info(f"Config dir ready: {CONFIG_DIR}")

    bus.publish("system.ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })
    log.info("system.ready published — config_manager online")


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — shutting down config_manager.")
    bus.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

bus = BusClient(module_name=MODULE_NAME)


def run() -> None:
    bus.subscribe("system.readytostart", on_system_readytostart)
    bus.subscribe("system.start",        on_system_start)
    bus.subscribe("system.stop",         on_system_stop)
    bus.subscribe("config.get",          on_config_get)
    bus.subscribe("config.set",          on_config_set)

    log.info("config_manager ready — waiting for messages...")
    bus.start(blocking=True)


if __name__ == "__main__":
    run()
