"""
Web Browser Head Unit — Logger System

Key Features:
  1. Console Logging: Built on `loguru` writing to stdout with asynchronous non-blocking queue (`enqueue=True`).
  2. WebSocket Log Stream: Streams log entries over WebSocket ONLY if at least one client is connected (`len(connected_clients) > 0`).
  3. No ZMQ Bus Logging: Removes log entries from the common ZMQ bus to save IPC bandwidth.
"""

import asyncio
import json
import os
import sys
import threading
from typing import Set

from loguru import logger as _root_logger

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

# Configure loguru console sink
_root_logger.remove()
_CONSOLE_SINK_ID = _root_logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{extra[module_name]}</cyan> - <level>{message}</level>",
    level="DEBUG" if os.getenv("DEBUG") else "INFO",
    enqueue=True,
)


class WebSocketLogServer:
    """Standalone WebSocket Log Server streaming loguru records to connected web clients."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8766):
        self.host = host
        self.port = port
        self.connected_clients: Set = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        if not HAS_WEBSOCKETS:
            return

        def _run_server():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def _handler(ws):
                self.connected_clients.add(ws)
                try:
                    await ws.wait_closed()
                finally:
                    self.connected_clients.remove(ws)

            async def _main():
                async with websockets.serve(_handler, self.host, self.port):
                    await asyncio.Event().wait()

            try:
                self._loop.run_until_complete(_main())
            except Exception:
                pass

        self._thread = threading.Thread(target=_run_server, daemon=True, name="ws_log_server")
        self._thread.start()

    def has_clients(self) -> bool:
        return len(self.connected_clients) > 0

    def broadcast_log(self, log_dict: dict):
        if not self.has_clients() or not self._loop or not HAS_WEBSOCKETS:
            return  # Skip formatting and broadcasting if no client is connected

        message = json.dumps(log_dict)
        for ws in list(self.connected_clients):
            try:
                asyncio.run_coroutine_threadsafe(ws.send(message), self._loop)
            except Exception:
                pass


# Global WS Log Server instance
ws_log_server = WebSocketLogServer()
ws_log_server.start()


def _ws_sink(message):
    """Loguru sink function: forwards logs to WebSocket server ONLY if clients are connected."""
    if not ws_log_server.has_clients():
        return  # Zero overhead when no client is listening

    record = message.record
    log_dict = {
        "timestamp": record["time"].strftime("%H:%M:%S"),
        "level": record["level"].name,
        "module": record["extra"].get("module_name", "root"),
        "message": record["message"],
    }
    ws_log_server.broadcast_log(log_dict)


# Register WebSocket sink with loguru
_root_logger.add(_ws_sink, enqueue=True)


def get_logger(module_name: str):
    """Returns a bound loguru logger scoped to `module_name`."""
    return _root_logger.bind(module_name=module_name)
