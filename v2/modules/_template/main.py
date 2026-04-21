"""
NemoHeadUnit-Wireless v2 — Module Template

Copy this folder to start a new module:
    cp -r v2/modules/_template v2/modules/<your_module_name>

Then follow these steps:
  1. Set MODULE_NAME below to your module name (must match the folder name)
  2. Fill in the contract docstring (Subscribes / Publishes / Config keys)
  3. Declare your config keys and defaults in _DEFAULTS (if needed)
  4. Implement on_system_start, on_system_stop and your topic handlers
  5. Add subscriptions in run()
  6. Keep ALL internal logic inside this folder
  7. Verify standalone: python v2/modules/<your_module_name>/main.py
  8. Verify autodiscovery: python v2/main.py

---
Module contract (fill this in):

  Name        : <module_name>
  Subscribes  : system.start
                system.stop
                config.response   (auto-handled by ConfigClient)
                config.changed    (auto-handled by ConfigClient)
                <other.topics>    → {payload description}
  Publishes   : <topic.name>      → {payload description}
  Config keys : <key>             type    default   description
  State       : private
---

Path layout (auto-configured below):
  v2/
  ├── shared/           ← BusClient, ConfigClient, logger
  └── modules/
      └── <module>/     ← THIS file lives here
          └── main.py

sys.path includes:
  v2/          → from shared.bus_client import BusClient
               → from shared.config_client import ConfigClient
  v2/modules/  → from <module_name>.subfile import Foo
"""

import sys
from pathlib import Path

_HERE    = Path(__file__).parent        # v2/modules/<module_name>/
_MODULES = _HERE.parent                 # v2/modules/
_V2      = _MODULES.parent              # v2/

if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from shared.bus_client import BusClient        # noqa: E402
from shared.config_client import ConfigClient  # noqa: E402
from shared.logger import get_logger           # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "_template"  # ← STEP 1: change this to your module name

log = get_logger(MODULE_NAME)
bus = BusClient(module_name=MODULE_NAME)
cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# STEP 2: Config defaults
# ---------------------------------------------------------------------------
# Declare every key your module reads from config_manager.
# These values are used immediately at startup; config_manager will override
# them asynchronously once it responds to the config.get request.
#
# Remove _DEFAULTS and cfg entirely if your module has no configuration.

_DEFAULTS = {
    # "my_key": "default_value",
    # "timeout": 10,
    # "enabled": True,
}

_config: dict = dict(_DEFAULTS)

# ---------------------------------------------------------------------------
# STEP 3: ConfigClient callbacks
# ---------------------------------------------------------------------------
# _on_config_loaded  → called once with the full persisted config dict
# _on_config_changed → called each time a single key is updated at runtime
#
# Both are no-ops if _DEFAULTS is empty — safe to leave as-is.

def _on_config_loaded(config: dict) -> None:
    global _config
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in config.items() if k in _DEFAULTS})
    _config = merged
    log.info(f"Config loaded: {_config}")
    # TODO: apply config to live state if needed


def _on_config_changed(key: str, value) -> None:
    if key not in _DEFAULTS:
        log.warning(f"config.changed: unknown key '{key}' — ignoring")
        return
    _config[key] = value
    log.info(f"Config changed: {key} = {value!r}")
    # TODO: react to the change (e.g. reconfigure a subsystem)


# ---------------------------------------------------------------------------
# STEP 4: system lifecycle handlers
# ---------------------------------------------------------------------------

def on_system_start(topic: str, payload: dict) -> None:
    """Called when the orchestrator signals the whole system is up."""
    log.info(f"System started. Active modules: {payload.get('modules', [])}")
    # TODO: initialise resources, start background threads, etc.


def on_system_stop(topic: str, payload: dict) -> None:
    """Called when the orchestrator signals a graceful shutdown."""
    log.info("System stop — cleaning up...")
    # TODO: flush state, close resources, etc.
    bus.stop()


# ---------------------------------------------------------------------------
# STEP 4 (continued): topic handlers
# ---------------------------------------------------------------------------
# One function per subscribed topic. Naming convention: on_<snake_case_topic>
#
# Example:
#
# def on_some_event(topic: str, payload: dict) -> None:
#     value = payload.get("key")
#     bus.publish("<module_name>.result", {"value": value})


# ---------------------------------------------------------------------------
# STEP 5: publishing helper (optional)
# ---------------------------------------------------------------------------
# Use bus.publish() anywhere inside the module to emit events.
#
# Example:
#
# def _emit_ready() -> None:
#     bus.publish("<module_name>.ready", {"status": "ok"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

bus = BusClient(module_name=MODULE_NAME)


def run() -> None:
    # Register ConfigClient callbacks and subscribe config.response/changed
    # Remove these two lines if your module has no configuration.
    cfg.on_config_loaded  = _on_config_loaded
    cfg.on_config_changed = _on_config_changed
    cfg.register()

    # Core lifecycle
    bus.subscribe("system.start", on_system_start)
    bus.subscribe("system.stop",  on_system_stop)

    # STEP 5: add your topic subscriptions here
    # bus.subscribe("some.topic", on_some_event)

    # Request persisted config from config_manager.
    # _DEFAULTS are active until config.response arrives.
    # Remove this line if your module has no configuration.
    cfg.get()

    log.info("Module started, waiting for messages...")
    bus.start(blocking=True)  # blocks here — receive loop


if __name__ == "__main__":
    run()
