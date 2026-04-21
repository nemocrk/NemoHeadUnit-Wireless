"""
NemoHeadUnit-Wireless v2 — config_manager module

Centralised configuration service. Persists per-module settings to YAML files
and notifies subscribers when a value changes.

---
Module contract:

  Name        : config_manager
  Subscribes  : system.start
                system.stop
                config.get      → {"module": "<name>", "requester": "<who>" (optional)}
                config.set      → {"module": "<name>", "key": "<k>", "value": <v>}
  Publishes   : config.response → {"module": "<name>", "config": {<key>: <value>, ...},
                                    "requester": "<who>" (echoed, empty string if absent)}
                config.changed  → {"module": "<name>", "key": "<k>", "value": <v>}

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
  - config.get always returns the full config dict for the requested module
    (empty dict if no config exists yet).
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

import yaml  # noqa: E402  (PyYAML — available in conda py314 env)

from shared.bus_client import BusClient  # noqa: E402
from shared.logger import get_logger     # noqa: E402

# ---------------------------------------------------------------------------
# Module identity & paths
# ---------------------------------------------------------------------------

MODULE_NAME = "config_manager"

log = get_logger(MODULE_NAME)

# Config files are stored relative to the v2/ root so they survive restarts.
CONFIG_DIR = _V2 / "config"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _config_path(module: str) -> Path:
    """Return the YAML path for a given module name."""
    return CONFIG_DIR / f"{module}.yaml"


def _load_config(module: str) -> dict:
    """Load and return the full config dict for *module*. Returns {} on miss."""
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
    """Persist *data* as YAML for *module*. Returns True on success."""
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
    """
    Handles config.get requests.

    Expected payload: {"module": "<module_name>", "requester": "<who>" (optional)}
    Responds on config.response echoing the requester field so subscribers
    can filter responses meant for them.
    """
    module    = payload.get("module")
    requester = payload.get("requester", "")  # echo back to allow filtering

    if not module:
        log.warning("config.get received without 'module' field — ignoring.")
        return

    config = _load_config(module)
    log.info(f"config.get for '{module}' (requester='{requester}') → {len(config)} keys")

    bus.publish("config.response", {
        "module":    module,
        "config":    config,
        "requester": requester,
    })


def on_config_set(topic: str, payload: dict):
    """
    Handles config.set requests.

    Expected payload: {"module": "<module_name>", "key": "<k>", "value": <v>}
    Persists the new value and broadcasts config.changed.
    """
    module = payload.get("module")
    key    = payload.get("key")
    value  = payload.get("value")

    if not module or key is None:
        log.warning(f"config.set missing 'module' or 'key': {payload} — ignoring.")
        return

    data = _load_config(module)
    data[key] = value

    if not _save_config(module, data):
        return  # error already logged in _save_config

    log.info(f"config.set '{module}'.{key} = {value!r}")

    bus.publish("config.changed", {
        "module": module,
        "key":    key,
        "value":  value,
    })


def on_system_start(topic: str, payload: dict):
    log.info(f"System started — config dir: {CONFIG_DIR}")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def on_system_stop(topic: str, payload: dict):
    log.info("System stop received — shutting down config_manager.")
    bus.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

bus = BusClient(module_name=MODULE_NAME)


def run():
    bus.subscribe("system.start",  on_system_start)
    bus.subscribe("system.stop",   on_system_stop)
    bus.subscribe("config.get",    on_config_get)
    bus.subscribe("config.set",    on_config_set)

    log.info("config_manager ready — waiting for messages...")
    bus.start(blocking=True)


if __name__ == "__main__":
    run()
