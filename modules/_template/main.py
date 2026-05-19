"""
NemoHeadUnit-Wireless — Module Template

Copy this folder to start a new module:
    cp -r modules/_template modules/<your_module_name>

Then follow these steps:
  1. Set MODULE_NAME to your module name (must match the folder name)
  2. Set PRIORITY (see Boot Protocol below)
  3. Fill in the contract docstring
  4. Declare config schema in _SCHEMA using the field_* helpers.
     _SCHEMA drives three things:
       - first-boot seeding of default values (config_manager reads field.default)
       - config_ui widget rendering (typed widgets: QSpinBox, QComboBox, …)
       - runtime validation of config.set calls
     Remove _SCHEMA (and all cfg references) if the module has no configuration.
  5. Implement on_system_start, on_system_stop and your topic handlers
  6. Add subscriptions in run()
  7. Keep ALL internal logic inside this folder
  8. Verify standalone: python modules/<your_module_name>/main.py
  9. Verify autodiscovery: python main.py

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
  root/
  ├── shared/           ← BusClient, ConfigClient, logger, config_schema
  └── modules/
      └── <module>/     ← THIS file lives here
          └── main.py

sys.path includes:
  root/         → from shared.bus_client import BusClient
                → from shared.config_client import ConfigClient
                → from shared.config_schema import field_int, field_enum, ...
  root/modules/ → from <module_name>.subfile import Foo
"""

import sys
from pathlib import Path
import time

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_HERE      = Path(__file__).parent   # modules/<module_name>/
_MODULES   = _HERE.parent            # modules/
_REPO_ROOT = _MODULES.parent         # root

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from shared.bus_client import BusClient        # noqa: E402
from shared.config_client import ConfigClient  # noqa: E402
from shared.logger import get_logger           # noqa: E402
from shared.config_schema import (             # noqa: E402
    field_string, field_int, field_float, field_enum, field_bool,
)

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "_template"  # ← STEP 1: change to your module name
PRIORITY: int = 1           # ← STEP 2: 0=infrastructure, 1=services, 2=UI

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)
cfg = ConfigClient(bus=bus, module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# STEP 3: Config schema
# ---------------------------------------------------------------------------

_SCHEMA = {
    # "my_key":  field_string(default="default_value"),
    # "timeout": field_int(default=10, min=1, max=300),
    # "mode":    field_enum(default="auto", choices=["off", "auto", "on"]),
    # "enabled": field_bool(default=True),
}

_config: dict = {k: v.default for k, v in _SCHEMA.items()}

# ---------------------------------------------------------------------------
# STEP 4: ConfigClient callbacks
# ---------------------------------------------------------------------------

def _on_config_loaded(config: dict) -> None:
    global _config
    if not config:
        log.info("No persisted config found — defaults seeded by config_manager.")
        return
    merged = {k: v.default for k, v in _SCHEMA.items()}
    merged.update({k: v for k, v in config.items() if k in _SCHEMA and not isinstance(v, (dict, list))})
    _config = merged
    log.info(f"Config loaded: {_config}")


def _on_config_changed(key: str, value) -> None:
    if key not in _SCHEMA:
        log.warning(f"config.changed: unknown key '{key}' — ignoring")
        return
    if isinstance(value, (dict, list)):
        log.warning(f"config.changed: structural value for '{key}' rejected")
        return
    _config[key] = value
    log.info(f"Config changed: {key} = {value!r}")


# ---------------------------------------------------------------------------
# STEP 5: Boot protocol handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart received — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {"name": MODULE_NAME, "priority": PRIORITY})


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return
    log.info(f"system.start priority={PRIORITY} received — initialising...")
    cfg.get(schema=_SCHEMA)
    bus.publish("system.ready", {"name": MODULE_NAME, "priority": PRIORITY})
    log.info(f"system.ready published (priority={PRIORITY})")


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop — cleaning up...")
    bus.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    cfg.on_config_loaded  = _on_config_loaded
    cfg.on_config_changed = _on_config_changed
    cfg.register()

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
        pass


if __name__ == "__main__":
    run()
