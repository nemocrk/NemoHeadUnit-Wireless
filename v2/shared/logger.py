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
import subprocess
import threading
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
        if self._bus._running:
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
        logger_obj.logger.info(f"Attaching bus handler to logger '{logger_obj.name}'")
        logger_obj._attach_bus_handler(_bus_handler)

# ---------------------------------------------------------------------------
# Internal helper for subprocess execution with real-time logging
# ---------------------------------------------------------------------------

def run_subprocess_and_log(logger: Logger, *popenargs,
    input=None, capture_output=False, timeout=None, check=False, **kwargs):
    """Run a subprocess and log its output in real time.

    Args:
        *popenargs: Positional arguments for subprocess.Popen
        input: Optional input to send to the subprocess
        capture_output: If True, captures stdout and stderr and returns them
        timeout: Optional timeout in seconds
        check: If True, raises CalledProcessError on non-zero exit code
        **kwargs: Additional keyword arguments for subprocess.Popen

    Returns:
        CompletedProcess instance with stdout and stderr if capture_output is True
    """
    # Ensure text=True for real-time logging. 
    # We pop these from kwargs to avoid "multiple values for keyword argument" errors
    # if the caller also tries to provide them.
    kwargs.pop("text", None)
    bufsize = kwargs.pop("bufsize", 1)

    process = subprocess.Popen(*popenargs,
        stdin=subprocess.PIPE if input else None,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=True, bufsize=bufsize, **kwargs)

    try:
        if input:
            process.stdin.write(input)
            process.stdin.close()

        stdout_lines = []
        stderr_lines = []

        def reader(pipe, log_func, prefix, accumulator):
            for line in iter(pipe.readline, ""):
                if line:
                    log_func(f"{prefix} {line.strip()}")
                    accumulator.append(line)
            pipe.close()

        if capture_output:
            t1 = threading.Thread(target=reader, args=(process.stdout, logger.info, "[subprocess stdout]", stdout_lines))
            t2 = threading.Thread(target=reader, args=(process.stderr, logger.error, "[subprocess stderr]", stderr_lines))
            t1.start()
            t2.start()
            
            return_code = process.wait(timeout=timeout)
            t1.join()
            t2.join()
        else:
            return_code = process.wait(timeout=timeout)
        if check and return_code != 0:
            raise subprocess.CalledProcessError(return_code, popenargs[0],
                output=''.join(stdout_lines) if capture_output else None,
                stderr=''.join(stderr_lines) if capture_output else None)
        return subprocess.CompletedProcess(popenargs[0], return_code,
            stdout=''.join(stdout_lines) if capture_output else None,
            stderr=''.join(stderr_lines) if capture_output else None)
    except Exception as e:
        logger.error(f"Subprocess execution failed: {e}")
        raise

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
            self.logger.info(f"Attaching bus handler to logger '{name}'")
            self._attach_bus_handler(_bus_handler)
        else:
            self.logger.info(f"No bus handler to attach for logger '{name}' yet")

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

def get_logger(name: str, level: int = logging.INFO, bus: "BusClient" = None) -> Logger:
    """Get or create a logger for the given module name."""
    logger = LoggerManager.get_logger(name, level)
    logger.info(f"Logger '{name}' created with level {logging.getLevelName(level)}")
    if bus:
        logger.info(f"Starting attach_bus for logger '{name}'")
        attach_bus(bus)
    else:
        logger.info(f"No bus provided for logger '{name}' — skipping attach_bus")
    return logger


def set_verbosity(module_name: str, level: int) -> None:
    LoggerManager.set_verbosity(module_name, level)


def get_verbosity(module_name: str) -> int:
    return LoggerManager.get_verbosity(module_name)
