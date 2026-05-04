"""
NemoHeadUnit-Wireless v2 — Module Template

Copy this folder to start a new module:
    cp -r v2/modules/_template v2/modules/<your_module_name>

Then follow these steps:
  1. Set MODULE_NAME to your module name (must match the folder name)
  2. Set PRIORITY (see Boot Protocol below)
  3. Fill in the contract docstring
  4. Declare config keys in _DEFAULTS and their types in _SCHEMA
     (remove both if the module has no configuration)
  5. Implement on_system_start, on_system_stop and your topic handlers
  6. Add subscriptions in run()
  7. Keep ALL internal logic inside this folder
  8. Verify standalone: python v2/modules/<your_module_name>/main.py
  9. Verify autodiscovery: python v2/main.py

---
Boot Protocol (multi-step priority):

  main → system.readytostart           (broadcast, no payload)
  module → system.module_ready          {name, priority}
  main → system.start {priority: 0}     (level 0 modules init)
  module → system.ready {name, priority: 0}
  main → system.start {priority: 1}     (level 1 modules init)
  module → system.ready {name, priority: 1}
  ...
  main → system.stop                    (broadcast, graceful shutdown)

Priority levels (convention):
  0  — infrastructure   (config_manager, bus utilities)
  1  — services         (bluetooth, hostapd_helper, tcp_server, ...)
  2  — UI               (bluetooth_ui, config_ui, ...)

A module MUST:
  - respond to system.readytostart with system.module_ready
  - respond to system.start only when payload["priority"] == PRIORITY
  - publish system.ready after completing its own init
  - tolerate receiving system.start messages for other priority levels
    (simply ignore them)
---

Module contract (fill this in):

  Name        : <module_name>
  Priority    : 1
  Subscribes  : system.readytostart
                system.start
                system.stop
                config.response   (auto-handled by ConfigClient)
                config.changed    (auto-handled by ConfigClient)
                <other.topics>    → {payload description}
  Publishes   : system.module_ready → {name, priority}
                system.ready      → {name, priority}
                <topic.name>      → {payload description}
  Config keys : <key>             type    default   description
  State       : private
---

Path layout (auto-configured below):
  v2/
  ├── shared/           ← BusClient, ConfigClient, logger, config_schema
  └── modules/
      └── <module>/     ← THIS file lives here
          └── main.py

sys.path includes:
  v2/          → from shared.bus_client import BusClient
               → from shared.config_client import ConfigClient
               → from shared.config_schema import field_int, field_enum, ...
  v2/modules/  → from <module_name>.subfile import Foo
"""

import sys
from pathlib import Path
import time

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
from shared.config_schema import (             # noqa: E402
    field_string, field_int, field_float, field_enum,
)

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "_template"  # ← STEP 1: change to your module name

# Boot priority (see Boot Protocol in the docstring above).
# 0 = infrastructure, 1 = services, 2 = UI
PRIORITY: int = 1          # ← STEP 2: set your priority level

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)
cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# STEP 3: Config defaults and schema
# ---------------------------------------------------------------------------
# _DEFAULTS: raw values persisted to YAML on first boot.
# _SCHEMA:   describes the type and constraints of each key so that
#            config_ui renders the correct widget and config_manager
#            validates incoming config.set calls.
#
# Every key in _DEFAULTS should have a matching entry in _SCHEMA.
# Remove both _DEFAULTS and _SCHEMA (and all cfg references) if the
# module has no configuration.
#
# Available helpers:
#   field_string(default)                     → free-text QLineEdit
#   field_int(default, min=None, max=None)    → QLineEdit+±  or  Slider
#   field_float(default, min=None, max=None)  → QLineEdit+±  or  Slider
#   field_enum(default, choices=[...]         → QComboBox

_DEFAULTS = {
    # "my_key":  "default_value",
    # "timeout": 10,
    # "enabled": "on",
}

_SCHEMA = {
    # "my_key":  field_string(default="default_value"),
    # "timeout": field_int(default=10, min=1, max=300),
    # "enabled": field_enum(default="on", choices=["off", "on"]),
}

_config: dict = dict(_DEFAULTS)

# ---------------------------------------------------------------------------
# STEP 4: ConfigClient callbacks
# ---------------------------------------------------------------------------

def _on_config_loaded(config: dict) -> None:
    global _config
    if not config:
        log.info("No persisted config found — defaults seeded by config_manager.")
        return
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
# STEP 5: Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    """
    Orchestrator is ready to begin the multi-step boot.
    Announce this module's name and priority so main.py can build
    the startup plan before issuing system.start messages.
    """
    log.info(f"system.readytostart received — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })


def on_system_start(topic: str, payload: dict) -> None:
    """
    Orchestrator fires system.start for each priority level in order.
    Only act when payload["priority"] matches this module's PRIORITY.
    After completing init, publish system.ready so main.py can advance
    to the next priority level.
    """
    if payload.get("priority") != PRIORITY:
        return  # not our turn yet (or already past)

    log.info(f"system.start priority={PRIORITY} received — initialising...")

    # Request config from config_manager, passing both defaults (for first-boot
    # seeding) and schema (for config_ui typed widgets + validation).
    cfg.get(defaults=_DEFAULTS, schema=_SCHEMA)

    # Signal that this module is fully initialised.
    bus.publish("system.ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })
    log.info(f"system.ready published (priority={PRIORITY})")


def on_system_stop(topic: str, payload: dict) -> None:
    """Graceful shutdown — called for all modules simultaneously."""
    log.info("system.stop — cleaning up...")
    # TODO: flush state, close resources, stop background threads
    bus.stop()


# ---------------------------------------------------------------------------
# STEP 5 (continued): topic handlers
# ---------------------------------------------------------------------------
# One function per subscribed topic. Naming: on_<snake_case_topic>
#
# Example:
#
# def on_some_event(topic: str, payload: dict) -> None:
#     value = payload.get("key")
#     bus.publish("<module_name>.result", {"value": value})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    # Config callbacks — remove if no configuration needed
    cfg.on_config_loaded  = _on_config_loaded
    cfg.on_config_changed = _on_config_changed
    cfg.register()

    # Boot protocol
    bus.subscribe("system.readytostart", on_system_readytostart)
    bus.subscribe("system.start",        on_system_start)
    bus.subscribe("system.stop",         on_system_stop)

    # STEP 6: add your topic subscriptions here
    # bus.subscribe("some.topic", on_some_event)

    log.info("Module started, waiting for messages...")
    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()
    try:
        bus_thread.join()
    except KeyboardInterrupt:
        pass  # gestito dal main via system.stop


if __name__ == "__main__":
    run()
