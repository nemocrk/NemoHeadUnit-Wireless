"""
NemoHeadUnit-Wireless v2 — Logger
Per-module verbosity control — backed by loguru (async enqueue backend).

Usage:
    from shared.logger import get_logger
    log = get_logger("my_module")
    log.info("hello")
    log.set_verbosity("DEBUG")   # str level name or logging.DEBUG int

Bus forwarding (optional, call once from main.py or any entry point):
    from shared.logger import attach_bus
    attach_bus(bus)   # BusClient instance
    # All subsequent log calls on any Logger will also publish on log.entry

Handler architecture:

  Stdout sink (loguru, enqueue=True)
    log.info() returns immediately — loguru's internal thread owns stdout.
    Never blocks the calling module thread on I/O.

  Bus sink (loguru callback + dedicated drain thread)
    The loguru sink callback does only queue.put_nowait() — O(1), never blocks.
    A single dedicated daemon thread drains the queue and calls
    send_multipart() on its OWN ZMQ PUB socket, never shared with any
    BusClient instance.  This eliminates the send_multipart race condition
    that caused interleaved frames when two threads wrote to the same socket:
      symptom: topic='log.entry' payload=b'config.get'  (wrong payload)
               topic='config.get' payload=b'log.entry'  (wrong topic)
"""

import json
import logging  # kept only for LogLevel enum + set_verbosity int compatibility
import os
import queue
import subprocess
import sys
import threading
from enum import IntEnum
from typing import TYPE_CHECKING

import zmq
from loguru import logger as _root_logger

if TYPE_CHECKING:
    from shared.bus_client import BusClient

BROKER_PUB_ADDR = "ipc:///tmp/nemobus_v2.pub"

# ---------------------------------------------------------------------------
# Global level — honoured by DEBUG env var
# ---------------------------------------------------------------------------

_DEFAULT_LEVEL: str = "DEBUG" if os.getenv("DEBUG") else "INFO"

# Remove loguru's default stderr sink and replace with an async stdout sink.
# enqueue=True means all writes happen in a background thread — module
# threads are never blocked on I/O when calling log.info() etc.
_root_logger.remove()
_CONSOLE_SINK_ID: int = _root_logger.add(
    sys.stdout,
    level=_DEFAULT_LEVEL,
    colorize=True,
    format=(
        "<green>{time:HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{extra[module]:<22}</cyan> | "
        "{message}"
    ),
    enqueue=True,   # async: write thread never blocks callers
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


# Map stdlib int levels → loguru string levels
_INT_TO_STR: dict[int, str] = {
    logging.DEBUG:    "DEBUG",
    logging.INFO:     "INFO",
    logging.WARNING:  "WARNING",
    logging.ERROR:    "ERROR",
    logging.CRITICAL: "CRITICAL",
}


def _level_str(level: int | str) -> str:
    """Normalise an int or string level to the loguru string form."""
    if isinstance(level, str):
        return level.upper()
    return _INT_TO_STR.get(level, "INFO")


# ---------------------------------------------------------------------------
# Bus sink state — dedicated queue + drain thread, created by attach_bus()
# ---------------------------------------------------------------------------

_bus_sink_id:     int | None            = None
_bus_queue:       queue.SimpleQueue     = queue.SimpleQueue()
_bus_drain_thread: threading.Thread | None = None
_bus_running:     bool                  = False
_bus_zmq_ctx:     zmq.Context | None    = None
_bus_zmq_pub:     zmq.Socket | None     = None


def attach_bus(bus: "BusClient") -> None:  # noqa: ARG001  (bus param kept for API compat)
    """
    Set up the bus log sink.

    Creates a DEDICATED ZMQ PUB socket (never shared with any BusClient)
    and a single daemon drain thread that is the sole caller of
    send_multipart().  The loguru sink callback only does queue.put_nowait()
    — O(1), never blocks the module thread.

    Safe to call multiple times — stops the previous drain thread first.
    The ``bus`` parameter is kept for API compatibility but is no longer
    used: the sink manages its own ZMQ connection.

    Call once after the ZMQ broker is running (e.g. from main.py).
    """
    global _bus_sink_id, _bus_queue, _bus_drain_thread
    global _bus_running, _bus_zmq_ctx, _bus_zmq_pub

    # ------------------------------------------------------------------
    # Tear down previous sink if attach_bus() is called more than once
    # ------------------------------------------------------------------
    if _bus_sink_id is not None:
        try:
            _root_logger.remove(_bus_sink_id)
        except Exception:  # noqa: BLE001
            pass
        _bus_sink_id = None

    if _bus_running:
        _bus_running = False
        if _bus_drain_thread is not None:
            _bus_drain_thread.join(timeout=1.0)
        if _bus_zmq_pub is not None:
            _bus_zmq_pub.close(linger=0)
        if _bus_zmq_ctx is not None:
            _bus_zmq_ctx.term()

    # ------------------------------------------------------------------
    # Fresh queue, dedicated ZMQ socket, drain thread
    # ------------------------------------------------------------------
    _bus_queue    = queue.SimpleQueue()
    _bus_running  = True
    _bus_zmq_ctx  = zmq.Context()
    _bus_zmq_pub  = _bus_zmq_ctx.socket(zmq.PUB)
    _bus_zmq_pub.setsockopt(zmq.SNDHWM, 5000)
    _bus_zmq_pub.setsockopt(zmq.LINGER, 0)
    _bus_zmq_pub.connect(BROKER_PUB_ADDR)

    def _drain() -> None:
        """Sole owner of _bus_zmq_pub — never races with any other thread."""
        while _bus_running:
            try:
                payload = _bus_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                _bus_zmq_pub.send_multipart(
                    [b"log.entry", json.dumps(payload).encode()],
                    flags=zmq.NOBLOCK,
                )
            except Exception:  # noqa: BLE001
                pass  # never let bus errors break logging

    _bus_drain_thread = threading.Thread(
        target=_drain, daemon=True, name="BusLogSink-drain"
    )
    _bus_drain_thread.start()

    # ------------------------------------------------------------------
    # Loguru sink: only enqueues — never touches the ZMQ socket directly
    # ------------------------------------------------------------------
    def _bus_sink(message) -> None:  # loguru Message object
        try:
            r = message.record
            _bus_queue.put_nowait({
                "module":  r["extra"].get("module", r["name"]),
                "level":   r["level"].name,
                "message": r["message"],
                "ts":      r["time"].timestamp(),
            })
        except Exception:  # noqa: BLE001
            pass

    _bus_sink_id = _root_logger.add(
        _bus_sink,
        level=_DEFAULT_LEVEL,
        enqueue=True,
    )


# ---------------------------------------------------------------------------
# Logger — thin proxy around a loguru bound logger
# ---------------------------------------------------------------------------

class Logger:
    """
    Per-module logger with independent verbosity control.
    Each v2 module should obtain its instance via get_logger().

    The underlying loguru proxy is created via logger.bind(module=name),
    which tags every record with the module name.  All records flow
    through the single async stdout sink (and optional bus sink).
    """

    def __init__(self, name: str, level: int = logging.INFO) -> None:
        self.name   = name
        self._proxy = _root_logger.bind(module=name)
        self.level  = logging.DEBUG if os.getenv("DEBUG") else level

    # ------------------------------------------------------------------
    # Verbosity control
    # ------------------------------------------------------------------

    def set_verbosity(self, level: int | str) -> None:
        """Set the effective log level (reconfigures the global loguru root).

        Accepts stdlib int levels (logging.DEBUG etc.) or string names.
        Per-module filtering at the sink level is not supported by loguru's
        design; use the log_viewer UI filter for runtime filtering.
        """
        if isinstance(level, int):
            self.level = level
            level_str  = _level_str(level)
        else:
            level_str  = level.upper()
            self.level = getattr(logging, level_str, logging.INFO)
        # Update console sink to the new level
        _root_logger.remove(_CONSOLE_SINK_ID)
        globals()["_CONSOLE_SINK_ID"] = _root_logger.add(
            sys.stdout,
            level=level_str,
            colorize=True,
            format=(
                "<green>{time:HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{extra[module]:<22}</cyan> | "
                "{message}"
            ),
            enqueue=True,
        )

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
    """Centralised registry of all v2 loggers."""

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

    If ``bus`` is provided, :func:`attach_bus` is called immediately so
    this module's records are forwarded to the bus from the first log call.
    Passing ``bus=None`` is valid — call :func:`attach_bus` separately.
    """
    logger_instance = LoggerManager.get_logger(name, level)
    if bus is not None:
        attach_bus(bus)
    return logger_instance


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
    """Run a subprocess and log its stdout/stderr lines in real time."""
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
