"""
NemoHeadUnit-Wireless v2 — Logger
Per-module verbosity control. Adapted from app/logger.py for the v2 architecture.

Usage:
    from shared.logger import get_logger
    log = get_logger("my_module")
    log.info("hello")
    log.set_verbosity(logging.DEBUG)

Bus forwarding (optional, call once from main.py or any entry point):
    from shared.logger import attach_bus
    attach_bus(bus)   # BusClient instance
    # All subsequent log calls on any Logger will also publish on log.entry
"""

import logging
import time
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared.bus_client import BusClient

V2_LOG_FORMAT = "%(asctime)s [%(levelname)s] {%(name)s} %(message)s"
V2_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ---------------------------------------------------------------------------
# Bus handler
# ---------------------------------------------------------------------------

class BusLogHandler(logging.Handler):
    """
    logging.Handler that publishes every log record on the ZMQ bus
    as a `log.entry` message.

    Payload: {module: str, level: str, message: str, ts: float}

    This handler is intentionally silent on publish errors so that
    a broken bus never disrupts the logging flow.
    """

    def __init__(self, bus: "BusClient") -> None:
        super().__init__()
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._bus.publish("log.entry", {
                "module":  record.name,
                "level":   record.levelname,
                "message": record.getMessage(),
                "ts":      record.created,
            })
        except Exception:  # noqa: BLE001
            pass  # never let bus errors break logging


# ---------------------------------------------------------------------------
# Internal bus handler registry (module-level singleton)
# ---------------------------------------------------------------------------

_bus_handler: BusLogHandler | None = None


def attach_bus(bus: "BusClient") -> None:
    """
    Attach a BusLogHandler to every existing and future Logger instance.
    Call this once, after the ZMQ bus is connected (e.g. from main.py).

    Safe to call multiple times — subsequent calls replace the previous handler.
    """
    global _bus_handler
    _bus_handler = BusLogHandler(bus)
    # Attach to all already-created loggers
    for logger_obj in LoggerManager._loggers.values():
        logger_obj._attach_bus_handler(_bus_handler)


# ---------------------------------------------------------------------------
# Log level enum
# ---------------------------------------------------------------------------

class LogLevel(IntEnum):
    """Log level enumeration for verbosity control."""
    NOTSET   = logging.NOTSET
    DEBUG    = logging.DEBUG
    INFO     = logging.INFO
    WARNING  = logging.WARNING
    ERROR    = logging.ERROR
    CRITICAL = logging.CRITICAL


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class Logger:
    """
    Per-module logger with independent verbosity control.
    Each v2 module should obtain its logger via get_logger().
    """

    def __init__(self, name: str, level: int = logging.INFO) -> None:
        self.name   = name
        self.level  = level
        self.logger = logging.getLogger(name)
        self.logger.setLevel(self.level)
        self.logger.propagate = False

        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(V2_LOG_FORMAT, datefmt=V2_DATE_FORMAT))
            self.logger.addHandler(handler)

        # If a bus handler was already attached before this logger was created,
        # attach it immediately so we don’t miss early messages.
        if _bus_handler is not None:
            self._attach_bus_handler(_bus_handler)

    def _attach_bus_handler(self, handler: BusLogHandler) -> None:
        """Idempotent: removes any previous BusLogHandler before adding the new one."""
        self.logger.handlers = [
            h for h in self.logger.handlers if not isinstance(h, BusLogHandler)
        ]
        self.logger.addHandler(handler)

    def set_verbosity(self, level: int) -> None:
        self.logger.setLevel(level)
        self.level = level

    def get_verbosity(self) -> int:
        return self.level

    def enable_debug(self) -> None:
        self.set_verbosity(logging.DEBUG)

    def disable_debug(self) -> None:
        self.set_verbosity(logging.INFO)

    # Convenience methods
    def debug(self, msg: str, *args, **kwargs) -> None:
        self.logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        self.logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self.logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        self.logger.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs) -> None:
        self.logger.critical(msg, *args, **kwargs)


# ---------------------------------------------------------------------------
# LoggerManager
# ---------------------------------------------------------------------------

class LoggerManager:
    """Centralized registry of all v2 loggers."""

    _loggers: dict[str, Logger] = {}

    @classmethod
    def get_logger(cls, name: str, level: int = logging.INFO) -> Logger:
        if name not in cls._loggers:
            cls._loggers[name] = Logger(name, level)
        return cls._loggers[name]

    @classmethod
    def set_verbosity(cls, module_name: str, level: int) -> None:
        cls.get_logger(module_name).set_verbosity(level)

    @classmethod
    def set_all_verbosity(cls, level: int) -> None:
        for logger in cls._loggers.values():
            logger.set_verbosity(level)

    @classmethod
    def get_verbosity(cls, module_name: str) -> int:
        return cls.get_logger(module_name).get_verbosity()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_logger(name: str, level: int = logging.INFO) -> Logger:
    """Get or create a logger for the given module name."""
    return LoggerManager.get_logger(name, level)


def set_verbosity(module_name: str, level: int) -> None:
    LoggerManager.set_verbosity(module_name, level)


def get_verbosity(module_name: str) -> int:
    return LoggerManager.get_verbosity(module_name)
