"""
Web Browser Head Unit — Cross-Platform IPC Address Utility

Provides OS-agnostic ZMQ socket URI resolution supporting:
  - Linux/POSIX: POSIX domain sockets (`ipc:///tmp/nemobus_v2.{kind}`)
  - Windows: Central Loopback TCP sockets (`tcp://127.0.0.1:15000` / `15001`)
"""

import os
import sys

IS_WINDOWS = sys.platform.startswith("win") or os.name == "nt"

PUB_PORT = 15000
SUB_PORT = 15001


def get_bus_address(module_name: str = "system", kind: str = "pub") -> str:
    """
    Returns an OS-compatible central ZMQ socket URI.

    Parameters:
      - module_name: Optional module name (retained for backward compatibility)
      - kind: 'sub' (modules publish to broker XSUB) or 'pub' (modules subscribe from broker XPUB)
    """
    if IS_WINDOWS:
        port = SUB_PORT if kind == "sub" else PUB_PORT
        return f"tcp://127.0.0.1:{port}"
    else:
        # Note: 'sub' kind connects to broker's XSUB endpoint (.pub URI in ZMQ convention)
        endpoint = "pub" if kind == "sub" else "sub"
        return f"ipc:///tmp/nemobus_v2.{endpoint}"
