"""
NemoHeadUnit-Wireless v2 — Module Template

Copy this folder to create a new module:
    cp -r v2/modules/_template v2/modules/<your_module_name>

Then:
  1. Rename the module in MODULE_NAME below
  2. Fill in the contract docstring (subscribes / publishes)
  3. Implement handler functions for each topic
  4. Add subscriptions in run()
  5. Keep all internal logic inside this folder

---
Module contract (fill this in when implementing):

  Name        : <module_name>
  Subscribes  : system.start, system.stop, <other topics>
  Publishes   : <topics this module publishes>
  State       : private
---

Path layout (auto-configured below):
  v2/
  ├── shared/          ← shared helpers (BusClient, logger)
  └── modules/
      └── <module>/    ← THIS file lives here
          └── main.py

sys.path includes both:
  - v2/          → enables: from shared.bus_client import BusClient
  - v2/modules/  → enables: from <module_name>.subfile import Foo
"""

import sys
from pathlib import Path

_HERE    = Path(__file__).parent        # v2/modules/<module_name>/
_MODULES = _HERE.parent                 # v2/modules/
_V2      = _MODULES.parent              # v2/

# v2/ → shared.bus_client, shared.logger
if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))

# v2/modules/ → <module_name>.subfile (e.g. bluetooth.bluez_adapter)
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from shared.bus_client import BusClient   # noqa: E402
from shared.logger import get_logger      # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "_template"  # ← change this to your module name

log = get_logger(MODULE_NAME)

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def on_system_start(topic: str, payload: dict):
    """Called when main.py signals the whole system is up."""
    log.info(f"System started. Active modules: {payload.get('modules', [])}")
    # TODO: initialise resources, start background work, etc.


def on_system_stop(topic: str, payload: dict):
    """Called when main.py signals a graceful shutdown."""
    log.info("System stop received — cleaning up...")
    # TODO: flush state, close resources, etc.
    bus.stop()


# ---------------------------------------------------------------------------
# Example: publishing a message
# ---------------------------------------------------------------------------
# Uncomment and adapt when you need to publish:
#
# def some_internal_event():
#     bus.publish("<module_name>.some_event", {"key": "value"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

bus = BusClient(module_name=MODULE_NAME)


def run():
    bus.subscribe("system.start", on_system_start)
    bus.subscribe("system.stop", on_system_stop)
    # TODO: subscribe to other relevant topics here
    # bus.subscribe("some.topic", on_some_topic)

    log.info("Module started, waiting for messages...")
    bus.start(blocking=True)  # blocks here — receive loop


if __name__ == "__main__":
    run()
