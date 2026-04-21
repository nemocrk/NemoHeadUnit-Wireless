"""
NemoHeadUnit-Wireless — message_bus.py

Compatibility shim: re-exports BusClient as MessageBus so all existing
imports continue to work without changes during migration.

    from app.message_bus import MessageBus  # still valid

DEPRECATED: import directly from app.bus_client going forward.
"""

from app.bus_client import BusClient as MessageBus

__all__ = ["MessageBus"]
