"""
base_channel_module.py — Abstract base class for OAA channel modules.

All modules under v2/modules/channel_modules/ (video, audio, input, sensor)
must subclass BaseChannelModule and implement its abstract methods.

This class enforces the v2 module contract (boot protocol + bus lifecycle)
so that concrete channel modules only need to implement channel-specific logic.

---
Boot protocol (inherited from v2 template convention):

  main → system.readytostart
  module → system.module_ready  {name, priority}
  main → system.start {priority: N}
  module → system.ready         {name, priority}
  main → system.stop

---
Channel lifecycle (OAA-specific):

  oaa_control_channel → oaa.channel.open  {channel_id, av_type?, ...}
       module calls: on_channel_open(channel_id, descriptor)

  oaa_control_channel → oaa.channel.close {channel_id}
       module calls: on_channel_close(channel_id)

  bus (binary frame) → oaa.frame.<channel_id>  raw bytes
       module calls: on_frame(channel_id, data)

---
Subclass responsibilities:

  - Set MODULE_NAME and CHANNEL_ID as class attributes
  - Implement on_channel_open(channel_id, descriptor)
  - Implement on_channel_close(channel_id)
  - Implement on_frame(channel_id, data)
  - Optionally override get_schema() to expose config keys
  - Optionally override on_config_loaded() / on_config_changed()
"""

from __future__ import annotations

import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path bootstrap — works wherever this file is called from
# ---------------------------------------------------------------------------
_HERE        = Path(__file__).parent          # v2/modules/channel_modules/
_MODULES     = _HERE.parent                   # v2/modules/
_V2          = _MODULES.parent                # v2/

for _p in (_V2, _MODULES):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared.bus_client import BusClient        # noqa: E402
from shared.config_client import ConfigClient  # noqa: E402
from shared.logger import get_logger           # noqa: E402


class BaseChannelModule(ABC):
    """Abstract base for all OAA channel modules.

    Concrete subclasses must define:
        MODULE_NAME : str   — matches the folder name (e.g. "video")
        CHANNEL_ID  : int   — OAA channel number (e.g. 3 for video)
        PRIORITY    : int   — boot priority level (default 1 = services)

    Example skeleton::

        class VideoModule(BaseChannelModule):
            MODULE_NAME = "video"
            CHANNEL_ID  = 3
            PRIORITY    = 1

            def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
                ...

            def on_channel_close(self, channel_id: int) -> None:
                ...

            def on_frame(self, channel_id: int, data: bytes) -> None:
                ...
    """

    # Subclasses MUST override these
    MODULE_NAME: str = ""
    CHANNEL_ID:  int = -1
    PRIORITY:    int = 1

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        if not self.MODULE_NAME:
            raise ValueError(f"{type(self).__name__}: MODULE_NAME must be set")
        if self.CHANNEL_ID < 0:
            raise ValueError(f"{type(self).__name__}: CHANNEL_ID must be set")

        self.bus = BusClient(module_name=self.MODULE_NAME)
        self.log = get_logger(self.MODULE_NAME, bus=self.bus)
        self.cfg = ConfigClient(bus=self.bus, module_name=self.MODULE_NAME)

        # In-RAM config seeded from schema defaults
        schema = self.get_schema()
        self._config: dict[str, Any] = {k: v.default for k, v in schema.items()}

        self._channel_open: bool = False

    # ------------------------------------------------------------------
    # Config schema — override to expose config keys
    # ------------------------------------------------------------------

    def get_schema(self) -> dict:
        """Return the config schema dict for this module.

        Override to declare typed config fields via field_* helpers from
        shared.config_schema.  Return an empty dict if no config is needed.
        """
        return {}

    # ------------------------------------------------------------------
    # Config callbacks — override if needed
    # ------------------------------------------------------------------

    def on_config_loaded(self, config: dict) -> None:
        """Called once the persisted config is delivered from config_manager."""
        schema = self.get_schema()
        if not config:
            self.log.info("No persisted config — schema defaults in use.")
            return
        merged = {k: v.default for k, v in schema.items()}
        merged.update({
            k: v for k, v in config.items()
            if k in schema and not isinstance(v, (dict, list))
        })
        self._config = merged
        self.log.info(f"Config loaded: {self._config}")

    def on_config_changed(self, key: str, value: Any) -> None:
        """Called when a single config key is updated at runtime."""
        schema = self.get_schema()
        if key not in schema:
            self.log.warning(f"config.changed: unknown key '{key}' — ignoring")
            return
        if isinstance(value, (dict, list)):
            self.log.warning(f"config.changed: structural value for '{key}' rejected")
            return
        self._config[key] = value
        self.log.info(f"Config changed: {key} = {value!r}")

    # ------------------------------------------------------------------
    # Boot protocol handlers (v2 convention)
    # ------------------------------------------------------------------

    def _on_system_readytostart(self) -> None:
        self.log.info(f"system.readytostart — announcing priority {self.PRIORITY}")
        self.bus.publish("system.module_ready", {
            "name":     self.MODULE_NAME,
            "priority": self.PRIORITY,
        })

    def _on_system_start(self, topic: str, payload: dict) -> None:
        if payload.get("priority") != self.PRIORITY:
            return
        self.log.info(f"system.start priority={self.PRIORITY} — initialising...")
        schema = self.get_schema()
        if schema:
            self.cfg.get(schema=schema)
        self._init()
        self.bus.publish("system.ready", {
            "name":     self.MODULE_NAME,
            "priority": self.PRIORITY,
        })
        self.log.info(f"system.ready published (priority={self.PRIORITY})")

    def _on_system_stop(self, topic: str, payload: dict) -> None:
        self.log.info("system.stop — cleaning up...")
        self._cleanup()
        self.bus.stop()

    # ------------------------------------------------------------------
    # OAA channel lifecycle bus handlers
    # ------------------------------------------------------------------

    def _on_oaa_channel_open(self, topic: str, payload: dict) -> None:
        if payload.get("channel_id") != self.CHANNEL_ID:
            return
        self._channel_open = True
        self.log.info(f"Channel {self.CHANNEL_ID} open — descriptor: {payload}")
        self.on_channel_open(self.CHANNEL_ID, payload)

    def _on_oaa_channel_close(self, topic: str, payload: dict) -> None:
        if payload.get("channel_id") != self.CHANNEL_ID:
            return
        self._channel_open = False
        self.log.info(f"Channel {self.CHANNEL_ID} closed")
        self.on_channel_close(self.CHANNEL_ID)

    def _on_oaa_frame(self, topic: str, data: bytes) -> None:
        """Receive a raw binary frame from the bus.

        The topic carries the channel id (oaa.frame.<channel_id>), but
        since we subscribe to our specific topic the channel_id here is
        always self.CHANNEL_ID.
        """
        if not self._channel_open:
            return
        self.on_frame(self.CHANNEL_ID, data)

    # ------------------------------------------------------------------
    # Abstract interface — MUST be implemented by subclasses
    # ------------------------------------------------------------------

    @abstractmethod
    def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
        """Called when the OAA control channel opens this channel.

        Args:
            channel_id:  OAA channel number (always == self.CHANNEL_ID).
            descriptor:  parsed channel descriptor dict from the SDR.
        """

    @abstractmethod
    def on_channel_close(self, channel_id: int) -> None:
        """Called when the OAA control channel closes this channel.

        Args:
            channel_id:  OAA channel number.
        """

    @abstractmethod
    def on_frame(self, channel_id: int, data: bytes) -> None:
        """Called for every binary frame received on this channel.

        Args:
            channel_id:  OAA channel number.
            data:        raw frame bytes (H.264 NAL unit, PCM block, etc.).
        """

    # ------------------------------------------------------------------
    # Optional hooks — override for module-specific init / teardown
    # ------------------------------------------------------------------

    def _init(self) -> None:
        """Called once during system.start, after config is requested.

        Override for one-time resource allocation (pipelines, sockets, etc.).
        """

    def _cleanup(self) -> None:
        """Called on system.stop before bus.stop().

        Override to flush state, close pipelines, release resources.
        """

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Connect to the bus, register subscriptions and block until stopped."""
        # Config callbacks
        schema = self.get_schema()
        if schema:
            self.cfg.on_config_loaded  = self.on_config_loaded
            self.cfg.on_config_changed = self.on_config_changed
            self.cfg.register()

        # Boot protocol
        self.bus.subscribe("system.readytostart", self._on_system_readytostart)
        self.bus.subscribe("system.start",        self._on_system_start)
        self.bus.subscribe("system.stop",         self._on_system_stop)

        # OAA channel lifecycle
        self.bus.subscribe("oaa.channel.open",  self._on_oaa_channel_open)
        self.bus.subscribe("oaa.channel.close", self._on_oaa_channel_close)

        # Raw frame topic: oaa.frame.<channel_id>
        frame_topic = f"oaa.frame.{self.CHANNEL_ID}"
        self.bus.subscribe(frame_topic, self._on_oaa_frame)

        self.log.info(
            f"{self.MODULE_NAME} started "
            f"(channel_id={self.CHANNEL_ID}, priority={self.PRIORITY}) "
            f"— waiting for messages..."
        )
        bus_thread = self.bus.start(blocking=False)
        time.sleep(0.05)
        self._on_system_readytostart()
        try:
            bus_thread.join()
        except KeyboardInterrupt:
            pass
