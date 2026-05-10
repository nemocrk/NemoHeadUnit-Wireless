"""
NemoHeadUnit-Wireless v2 — Logger
Per-module verbosity control — backed by logly (Rust async backend).

Usage:
    from shared.logger import get_logger
    log = get_logger("my_module")
    log.info("hello")
    log.set_verbosity("DEBUG")   # str level name

Bus forwarding (optional, call once from main.py or any entry point):
    from shared.logger import attach_bus
    attach_bus(bus)   # BusClient instance
    # All subsequent log calls on any Logger will also publish on log.entry
"""

import logging  # kept only for LogLevel enum compatibility
import os
import subprocess
import threading
from enum import IntEnum
from typing import TYPE_CHECKING

from logly import logger as _root_logger

if TYPE_CHECKING:
    from shared.bus_client import BusClient

# ---------------------------------------------------------------------------
# Global level — honoured by DEBUG env var
# ---------------------------------------------------------------------------

_DEFAULT_LEVEL: str = "DEBUG" if os.getenv("DEBUG") else "INFO"

# Configure logly root once at import time.
# async console output: the Rust backend batches writes so module threads
# are never blocked on a StreamHandler.emit() call.
_root_logger.configure(
    level=_DEFAULT_LEVEL,
    color=True,
    show_time=True,
    show_module=True,
    show_function=False,
    show_filename=False,
    show_lineno=False,
)

# ---------------------------------------------------------------------------
# Log level enum (stdlib-compatible values for callers using logging.DEBUG etc.)
# ---------------------------------------------------------------------------

class LogLevel(IntEnum):
    """Log level enumeration — values mirror stdlib logging for compatibility."""
    NOTSET   = logging.NOTSET
    DEBUG    = logging.DEBUG
    INFO     = logging.INFO
    WARNING  = logging.WARNING
    ERROR    = logging.ERROR
    CRITICAL = logging.CRITICAL


# Map stdlib int levels → logly string levels
_INT_TO_STR: dict[int, str] = {
    logging.DEBUG:    "DEBUG",
    logging.INFO:     "INFO",
    logging.WARNING:  "WARNING",
    logging.ERROR:    "ERROR",
    logging.CRITICAL: "CRITICAL",
}


def _level_str(level: int | str) -> str:
    """Normalise an int or string level to the logly string form."""
    if isinstance(level, str):
        return level.upper()
    return _INT_TO_STR.get(level, "INFO")


# ---------------------------------------------------------------------------
# Bus callback — registered once via attach_bus()
# ---------------------------------------------------------------------------

_bus_callback_id: int | None = None


def attach_bus(bus: "BusClient") -> None:
    """
    Register a logly callback that publishes every record on the ZMQ bus
    as a `log.entry` message.

    Safe to call multiple times — the previous callback is removed first.
    Call this once after the ZMQ bus is connected (e.g. from main.py).

    Payload: {module: str, level: str, message: str, ts: float}
    """
    global _bus_callback_id

    # Remove previous callback if any
    if _bus_callback_id is not None:
        try:
            _root_logger.remove_callback(_bus_callback_id)
        except Exception:  # noqa: BLE001
            pass
        _bus_callback_id = None

    def _bus_sink(record: dict) -> None:
        if not getattr(bus, "_running", False):
            return
        try:
            # logly timestamp is an ISO string; convert to float for
            # compatibility with the existing log_viewer payload contract.
            import datetime as _dt
            ts_raw = record.get("timestamp", "")
            try:
                ts = _dt.datetime.fromisoformat(ts_raw).timestamp()
            except Exception:  # noqa: BLE001
                import time
                ts = time.time()
            bus.publish("log.entry", {
                "module":  record.get("module", "unknown"),
                "level":   record.get("level",  "INFO").upper(),
                "message": record.get("message", ""),
                "ts":      ts,
            })
        except Exception:  # noqa: BLE001
            pass  # never let bus errors break logging

    _bus_callback_id = _root_logger.add_callback(_bus_sink)


# ---------------------------------------------------------------------------
# Logger — thin proxy around a logly bound logger
# ---------------------------------------------------------------------------

class Logger:
    """
    Per-module logger with independent verbosity control.
    Each v2 module should obtain its instance via get_logger().

    The underlying logly proxy is created via logger.bind(module=name),
    which tags every record with the module name without spawning a new
    root logger — all records still flow through the single Rust sink.
    """

    def __init__(self, name: str, level: int = logging.INFO) -> None:
        self.name  = name
        self._proxy = _root_logger.bind(module=name)
        # Level stored as int for set_verbosity() compatibility.
        # Actual filtering is done by reconfiguring the logly root.
        self.level = logging.DEBUG if os.getenv("DEBUG") else level

    # ------------------------------------------------------------------
    # Verbosity control
    # ------------------------------------------------------------------

    def set_verbosity(self, level: int | str) -> None:
        """Set this module's effective log level.

        Accepts stdlib int levels (logging.DEBUG etc.) or string names.
        Note: logly controls level globally; this reconfigures the root.
        For per-module filtering use the log_viewer UI filter instead.
        """
        if isinstance(level, int):
            self.level = level
            level_str  = _level_str(level)
        else:
            level_str  = level.upper()
            self.level = getattr(logging, level_str, logging.INFO)
        _root_logger.configure(level=level_str)

    def get_verbosity(self) -> int:
        return self.level

    def enable_debug(self) -> None:
        self.set_verbosity(logging.DEBUG)

    def disable_debug(self) -> None:
        self.set_verbosity(logging.INFO)

    # ------------------------------------------------------------------
    # Logging methods — mirror stdlib Logger API
    # ------------------------------------------------------------------

    def debug(self, msg: str, *args, **kwargs) -> None:
        self._proxy.debug(msg % args if args else msg)

    def info(self, msg: str, *args, **kwargs) -> None:
        self._proxy.info(msg % args if args else msg)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self._proxy.warning(msg % args if args else msg)

    def error(self, msg: str, *args, **kwargs) -> None:
        self._proxy.error(msg % args if args else msg)

    def critical(self, msg: str, *args, **kwargs) -> None:
        self._proxy.critical(msg % args if args else msg)


# ---------------------------------------------------------------------------
# LoggerManager — registry of per-module Logger instances
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
    """Get or create a logger for the given module name.

    If `bus` is provided, attach_bus() is called immediately so this
    module's records are forwarded to the bus from the first log call.
    Passing bus=None is valid — call attach_bus() separately at any time.
    """
    logger = LoggerManager.get_logger(name, level)
    if bus is not None:
        attach_bus(bus)
    return logger


def set_verbosity(module_name: str, level: int) -> None:
    LoggerManager.set_verbosity(module_name, level)


def get_verbosity(module_name: str) -> int:
    return LoggerManager.get_verbosity(module_name)


# ---------------------------------------------------------------------------
# Subprocess helper (kept for hostapd_helper and any other callers)
# ---------------------------------------------------------------------------

def run_subprocess_and_log(
    logger: Logger,
    *popenargs,
    input=None,
    capture_output=False,
    timeout=None,
    check=False,
    **kwargs,
):
    """Run a subprocess and log its stdout/stderr lines in real time.

    Args:
        logger: Logger instance used to emit subprocess output.
        *popenargs: Positional arguments forwarded to subprocess.Popen.
        input: Optional text input for the subprocess stdin.
        capture_output: If True, captures and returns stdout + stderr.
        timeout: Optional timeout in seconds.
        check: If True, raises CalledProcessError on non-zero exit code.
        **kwargs: Additional keyword arguments for subprocess.Popen.

    Returns:
        subprocess.CompletedProcess
    """
    kwargs.pop("text", None)
    bufsize = kwargs.pop("bufsize", 1)

    process = subprocess.Popen(
        *popenargs,
        stdin=subprocess.PIPE if input else None,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=True,
        bufsize=bufsize,
        **kwargs,
    )

    try:
        if input:
            process.stdin.write(input)
            process.stdin.close()

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def reader(pipe, log_func, prefix, accumulator):
            for line in iter(pipe.readline, ""):
                if line:
                    log_func(f"{prefix} {line.strip()}")
                    accumulator.append(line)
            pipe.close()

        if capture_output:
            t1 = threading.Thread(
                target=reader,
                args=(process.stdout, logger.debug, "[subprocess stdout]", stdout_lines),
            )
            t2 = threading.Thread(
                target=reader,
                args=(process.stderr, logger.error, "[subprocess stderr]", stderr_lines),
            )
            t1.start()
            t2.start()
            return_code = process.wait(timeout=timeout)
            t1.join()
            t2.join()
        else:
            return_code = process.wait(timeout=timeout)

        if check and return_code != 0:
            raise subprocess.CalledProcessError(
                return_code,
                popenargs[0],
                output="".join(stdout_lines) if capture_output else None,
                stderr="".join(stderr_lines) if capture_output else None,
            )
        return subprocess.CompletedProcess(
            popenargs[0],
            return_code,
            stdout="".join(stdout_lines) if capture_output else None,
            stderr="".join(stderr_lines) if capture_output else None,
        )
    except Exception as exc:
        logger.error(f"Subprocess execution failed: {exc}")
        raise
