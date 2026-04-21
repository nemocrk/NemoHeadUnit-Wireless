"""
NemoHeadUnit-Wireless v2 — Module Template

Copy this folder to create a new module:
    cp -r v2/modules/_template v2/modules/<your_module_name>

Then:
  1. Rename the module in MODULE_NAME below
  2. Define subscribed topics in SUBSCRIPTIONS
  3. Implement handler functions for each topic
  4. Define published topics in the docstring contract below
  5. Implement your business logic inside this file (or split into subfiles
     inside this folder — never import from sibling modules)

---
Module contract (fill this in when implementing):

  Name        : <module_name>
  Subscribes  : system.start, system.stop, <other topics>
  Publishes   : <topics this module publishes>
  State       : private
---
"""

import logging
import sys
from pathlib import Path

# Allow importing shared/ from the v2 root
sys.path.insert(0, str(Path(__file__).parents[2]))

from shared.bus_client import BusClient  # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "_template"  # ← change this to your module name

logging.basicConfig(
    level=logging.INFO,
    format=f"[{MODULE_NAME}] %(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(MODULE_NAME)

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
