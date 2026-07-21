"""
NemoHeadUnit-Wireless — Logger
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

  Semaphore cleanup
    loguru with enqueue=True allocates a multiprocessing.Queue internally
    for EACH sink, which holds a POSIX semaphore.  Without explicit removal
    both the console sink and the bus sink leak their semaphore at process
    exit, triggering:
      UserWarning: resource_tracker: There appear to be N leaked semaphore objects
    Fix: _atexit_cleanup() is registered once (at module import for the console
    sink, and inside attach_bus() for the bus sink).  It removes both sinks
    before the process dies, triggering loguru's internal Queue cleanup and
    releasing all POSIX semaphores cleanly.
    Works for normal exit and SIGTERM; SIGKILL is unrecoverable by design.
"""

import atexit
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
P2P_LOGS_ADDR = "ipc:///tmp/nemo_logs.ipc"

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

_bus_sink_id:       int | None          = None
_bus_queue:         queue.SimpleQueue   = queue.SimpleQueue()
_bus_drain_thread:  threading.Thread | None = None
_bus_running:       bool                = False
_bus_zmq_ctx:       zmq.Context | None  = None
_bus_zmq_pub:       zmq.Socket | None   = None
_atexit_registered: bool                = False


def _atexit_cleanup() -> None:
    """
    Registered via atexit.register() — runs before process exit.

    Removes BOTH loguru sinks (console + bus) so that their internal
    multiprocessing.Queue instances are closed and the underlying POSIX
    semaphores are released, silencing:

        UserWarning: resource_tracker: There appear to be N leaked semaphore objects

    Also stops the bus drain thread and closes the dedicated ZMQ socket.
    Safe to call multiple times (idempotent).
    """
    global _bus_sink_id, _bus_running, _bus_zmq_pub, _bus_zmq_ctx

    # --- Bus sink: remove first so loguru flushes + releases its Queue ---
    if _bus_sink_id is not None:
        try:
            _root_logger.remove(_bus_sink_id)
        except Exception:  # noqa: BLE001
            pass
        _bus_sink_id = None

    # Stop bus drain thread
    _bus_running = False
    if _bus_queue is not None:
        try:
            _bus_queue.put(None)
        except Exception:
            pass

    # Close ZMQ resources
    if _bus_zmq_pub is not None:
        try:
            _bus_zmq_pub.close(linger=0)
        except Exception:  # noqa: BLE001
            pass
        _bus_zmq_pub = None
    if _bus_zmq_ctx is not None:
        try:
            _bus_zmq_ctx.term()
        except Exception:  # noqa: BLE001
            pass
        _bus_zmq_ctx = None

    # --- Console sink: remove to release its enqueue semaphore too ---
    # _CONSOLE_SINK_ID may have been reassigned by set_verbosity(); read
    # from globals() to always get the current value.
    console_id = globals().get("_CONSOLE_SINK_ID")
    if console_id is not None:
        try:
            _root_logger.remove(console_id)
        except Exception:  # noqa: BLE001
            pass


# Register cleanup at module-import time so the console sink semaphore is
# always released even in processes that never call attach_bus().
atexit.register(_atexit_cleanup)
_atexit_registered = True   # prevents a second register() inside attach_bus()


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

    The atexit cleanup (_atexit_cleanup) is registered once at module-import
    time and covers both this sink and the console sink.

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
        if _bus_queue is not None:
            try:
                _bus_queue.put(None)
            except Exception:
                pass
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
    _bus_zmq_pub.connect(P2P_LOGS_ADDR)

    def _drain() -> None:
        """Sole owner of _bus_zmq_pub — never races with any other thread."""
        while _bus_running:
            try:
                payload = _bus_queue.get()
                if payload is None:
                    break
            except Exception:
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
    Each module should obtain its instance via get_logger().

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

    def exception(self, msg: str = "Exception", exc_info=None) -> None:
        """Log an exception with full traceback.

        Uses the active exception from sys.exc_info() when called inside an
        except block (the common case).  An explicit exception object can also
        be passed via *exc_info* to override the active context.

        This fixes the previous behaviour where passing a plain string while
        inside an except block would raise AttributeError because the old
        implementation called exc_info.__traceback__ on the string.
        """
        if exc_info is not None:
            # Caller supplied an explicit exception object.
            exc = exc_info
            tb = exc.__traceback__ if hasattr(exc, "__traceback__") else None
            self._proxy.opt(exception=(type(exc), exc, tb)).error(msg)
        else:
            # Use the active exception context (standard usage inside except:).
            exc_type, exc_val, exc_tb = sys.exc_info()
            if exc_type is not None:
                self._proxy.opt(exception=(exc_type, exc_val, exc_tb)).error(msg)
            else:
                # No active exception — just log the message as an error.
                self._proxy.error(msg)

# ---------------------------------------------------------------------------
# LoggerManager — registry of per-module Logger instances
# ---------------------------------------------------------------------------

class LoggerManager:
    """Centralised registry of all loggers."""

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

    try:
        process = subprocess.Popen(
            *popenargs,
            stdin=subprocess.PIPE if input else None,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            text=True,
            bufsize=bufsize,
            **kwargs,
        )
    except Exception as exc:
        logger.error(f"Subprocess spawn failed: {exc}")
        raise

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
