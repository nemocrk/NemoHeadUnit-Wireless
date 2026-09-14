"""
Web Browser Head Unit — Logger System

Key Features:
  1. Console Logging: Built on `loguru` writing to stdout with asynchronous non-blocking queue (`enqueue=True`).
  2. Structured Listener Dispatch: Dispatches structured log dicts to active BaseBackendModule WebSocket handlers.
"""

import os
import sys
from typing import Callable, Set
from loguru import logger as _root_logger

# Configure loguru console sink
_root_logger.remove()
_root_logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[module_name]}</cyan> - <level>{message}</level>",
    level="DEBUG" if os.getenv("DEBUG") else "INFO",
    enqueue=True,
)

_log_listeners: Set[Callable[[dict], None]] = set()


def add_log_listener(callback: Callable[[dict], None]) -> None:
    """Registers a callback to receive structured log dicts."""
    _log_listeners.add(callback)


def remove_log_listener(callback: Callable[[dict], None]) -> None:
    """Removes a previously registered log listener callback."""
    _log_listeners.discard(callback)


def _dispatch_log_sink(message) -> None:
    """Loguru sink function: forwards structured records to active log listeners."""
    if not _log_listeners:
        return

    try:
        record = message.record
        log_dict = {
            "timestamp": record["time"].strftime("%H:%M:%S"),
            "level": record["level"].name,
            "module": record["extra"].get("module_name", "root"),
            "message": record["message"],
        }

        for cb in list(_log_listeners):
            try:
                cb(log_dict)
            except Exception:
                pass
    except Exception:
        pass


# Register dispatch sink with loguru
_root_logger.add(_dispatch_log_sink, enqueue=True)


def get_logger(module_name: str):
    """Returns a bound loguru logger scoped to `module_name`."""
    return _root_logger.bind(module_name=module_name)
