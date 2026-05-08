"""
base_channel_module.py — Abstract base class for AA channel modules.

All modules under v2/modules/channel_modules/ (video, audio, input, sensor)
must subclass BaseChannelModule and implement its abstract methods.

This class enforces the v2 module contract (boot protocol + bus lifecycle)
so that concrete channel modules only need to implement channel-specific logic.

---
Boot protocol (inherited from v2 template convention):

  main → channel_manager.module_readytostart
  module → channel_manager.module_ready  {name, priority}
  main → channel_manager.module_start {priority: N}
  module → channel_manager.module_ready         {name, priority}   ← emitted lazily
  main → channel_manager.module_stop

channel_manager.module_ready is emitted only when ALL of the following are true:
  1. _init() has completed
  2. on_config_loaded() has been called (persisted config applied)
  3. _is_ready() returns True (default: True)
  4. self.channel_config is not None (SDR lookup succeeded)

Subclasses that manage an external resource (stream, pipeline, socket)
can override _is_ready() to gate readiness on that resource being open.
All other logic stays in BaseChannelModule — no override needed.

---
CLI arguments (parsed at import time):

  --module-name    str   module name override (default: MODULE_NAME class attr)
  --channel-id     int   AA channel id (required for all channel modules)
  --sdr-bytes-hex  str   hex-encoded ServiceDiscoveryResponse bytes
                         used to populate self.channel_config

---
Channel lifecycle (AA-specific):

  aa_control_channel → aa.channel.open  {channel_id, av_type?, ...}
       module calls: on_channel_open(channel_id, descriptor)

  aa_control_channel → aa.channel.close {channel_id}
       module calls: on_channel_close(channel_id)

  bus (assembled frame from tcp_server) → aa.frame.ch<channel_id>
       payload: {channel_id, message_id, encrypted, payload_hex}
       - message_id already extracted by tcp_server (no struct.unpack needed)
       - payload_hex is the decrypted proto body only
       - encrypted is the echo of the original wire flag
       module calls: on_frame(channel_id, message_id, encrypted, data)

---
aa.frame.send contract:
    channel_id  : int   — AA channel
    message_id  : int   — 2-byte AA message identifier
    payload_hex : str   — proto body ONLY (no message_id prepended)
    encrypted   : bool  — echoed back from the received frame

---
Subclass responsibilities:

  - Set MODULE_NAME as a class attribute
  - Set CHANNEL_ID as class attribute fallback (overridden by --channel-id CLI)
  - Implement on_channel_open(channel_id, descriptor)
  - Implement on_channel_close(channel_id)
  - Implement on_frame(channel_id, message_id, encrypted, data)
  - Optionally override get_schema() to expose config keys
  - Optionally override on_config_loaded() / on_config_changed()
  - Optionally override _is_ready() to gate channel_manager.module_ready on a resource
"""

from __future__ import annotations

import argparse
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
_REPO        = _V2.parent                     # repo root

for _p in (_V2, _MODULES, _REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared.bus_client import BusClient        # noqa: E402
from shared.config_client import ConfigClient  # noqa: E402
from shared.logger import get_logger           # noqa: E402
from shared.proto_utils import channel_config_from_sdr, proto_to_dict  # noqa: E402

# ---------------------------------------------------------------------------
# Module-level CLI parsing
# ---------------------------------------------------------------------------
_cli_parser = argparse.ArgumentParser(add_help=False)
_cli_parser.add_argument("--module-name",   default=None)
_cli_parser.add_argument("--channel-id",    type=int, default=None)
_cli_parser.add_argument("--sdr-bytes-hex", default="")
_CLI_ARGS, _ = _cli_parser.parse_known_args()


class BaseChannelModule(ABC):
    """Abstract base for all AA channel modules.

    Concrete subclasses must define:
        MODULE_NAME : str   — matches the folder name (e.g. "video")
        CHANNEL_ID  : int   — fallback AA channel number if --channel-id not given
        PRIORITY    : int   — boot priority level (default 1 = services)

    After __init__:
        self.channel_config  — full channel dict from SDR, or None if lookup failed.
                               Hard-fails system.ready when None.
        self.CHANNEL_ID      — overridden by --channel-id CLI arg if provided.
    """

    MODULE_NAME: str = ""
    CHANNEL_ID:  int = -1
    PRIORITY:    int = 1

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        if _CLI_ARGS.module_name is not None:
            self.MODULE_NAME = _CLI_ARGS.module_name
        if _CLI_ARGS.channel_id is not None:
            self.CHANNEL_ID = _CLI_ARGS.channel_id

        if not self.MODULE_NAME:
            raise ValueError(f"{type(self).__name__}: MODULE_NAME must be set")

        self.bus = BusClient(module_name=self.MODULE_NAME)
        self.log = get_logger(self.MODULE_NAME, bus=self.bus)
        self.cfg = ConfigClient(bus=self.bus, module_name=self.MODULE_NAME)

        sdr_hex = _CLI_ARGS.sdr_bytes_hex
        if sdr_hex and self.CHANNEL_ID >= 0:
            self.channel_config: dict | None = channel_config_from_sdr(
                sdr_hex, self.CHANNEL_ID
            )
            if self.channel_config is None:
                self.log.error(
                    "channel_config_from_sdr: channel_id=%d not found in SDR — "
                    "system.ready will NOT be published",
                    self.CHANNEL_ID,
                )
        else:
            self.channel_config = None
            if not sdr_hex:
                self.log.warning(
                    "--sdr-bytes-hex not provided — channel_config is None, "
                    "system.ready will NOT be published"
                )

        schema = self.get_schema()
        self._config: dict[str, Any] = {k: v.default for k, v in schema.items()}

        self._channel_open: bool = False

        self._init_done:       bool = False
        self._config_loaded:   bool = False
        self._ready_published: bool = False

    # ------------------------------------------------------------------
    # Config schema
    # ------------------------------------------------------------------

    def get_schema(self) -> dict:
        return {}

    # ------------------------------------------------------------------
    # Config callbacks
    # ------------------------------------------------------------------

    def on_config_loaded(self, config: dict) -> None:
        schema = self.get_schema()
        if not config:
            self.log.info("No persisted config — schema defaults in use.")
        else:
            merged = {k: v.default for k, v in schema.items()}
            merged.update({
                k: v for k, v in config.items()
                if k in schema and not isinstance(v, (dict, list))
            })
            self._config = merged
            self.log.info(f"Config loaded: {self._config}")
        self._config_loaded = True
        self._try_publish_ready()

    def on_config_changed(self, key: str, value: Any) -> None:
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
    # Readiness gate
    # ------------------------------------------------------------------

    def _is_ready(self) -> bool:
        return True

    def _try_publish_ready(self) -> None:
        if self._ready_published:
            return
        if self.channel_config is None:
            self.log.error(
                "_try_publish_ready: channel_config is None — "
                "system.ready will NOT be published for %s",
                self.MODULE_NAME,
            )
            return
        has_schema = bool(self.get_schema())
        config_ok  = (not has_schema) or self._config_loaded
        if not (self._init_done and config_ok and self._is_ready()):
            return
        self._ready_published = True
        self.bus.publish("channel_manager.module_ready", {
            "name":     self.MODULE_NAME,
            "priority": self.PRIORITY,
        })
        self.log.info(f"channel_manager.module_ready published (priority={self.PRIORITY})")

    # ------------------------------------------------------------------
    # Boot protocol handlers
    # ------------------------------------------------------------------

    def _on_channel_manager_module_readytostart(self) -> None:
        self.log.info(f"channel_manager.module_ready_to_start — announcing priority {self.PRIORITY}")
        self.bus.publish("channel_manager.module_ready_to_start", {
            "name":     self.MODULE_NAME,
            "priority": self.PRIORITY,
        })

    def _on_channel_manager_module_start(self, topic: str, payload: dict) -> None:
        if payload.get("name") != self.MODULE_NAME:
            return
        self.log.info(f"channel_manager.module_start name={self.MODULE_NAME} priority={self.PRIORITY} — initialising...")
        schema = self.get_schema()
        if schema:
            self.cfg.get(schema=schema)
        self._init()
        self._init_done = True
        self._try_publish_ready()

    def _on_channel_manager_module_stop(self, topic: str, payload: dict) -> None:
        self.log.info("channel_manager.module_stop — cleaning up...")
        self._cleanup()
        self.bus.stop()

    # ------------------------------------------------------------------
    # AA channel lifecycle bus handlers
    # ------------------------------------------------------------------

    def _on_aa_channel_open(self, topic: str, payload: dict) -> None:
        if payload.get("channel_id") != self.CHANNEL_ID:
            return
        self._channel_open = True
        self.log.info(f"Channel {self.CHANNEL_ID} open — descriptor: {payload}")
        self.on_channel_open(self.CHANNEL_ID, payload)

    def _on_aa_channel_close(self, topic: str, payload: dict) -> None:
        if payload.get("channel_id") != self.CHANNEL_ID:
            return
        self._channel_open = False
        self.log.info(f"Channel {self.CHANNEL_ID} closed")
        self.on_channel_close(self.CHANNEL_ID)

    def _on_aa_frame(self, topic: str, payload: dict) -> None:
        """Receive a fully assembled, decrypted frame from tcp_server.

        tcp_server already extracts message_id and strips it from the payload,
        so no struct.unpack is needed here.

        Payload contract: {channel_id, message_id, encrypted, payload_hex}
        """
        try:
            channel_id = int(payload["channel_id"])
            message_id = int(payload["message_id"])
            encrypted  = bool(payload.get("encrypted", False))
            body       = bytes.fromhex(payload["payload_hex"])
        except (KeyError, ValueError) as exc:
            self.log.error("_on_aa_frame: malformed payload — %s", exc)
            return

        self.log.debug(
            "_on_aa_frame: ch=%d msg=0x%04x enc=%s body_len=%d",
            channel_id, message_id, encrypted, len(body),
        )
        if not self._channel_open:
            self.log.warning(f"Frame dropped — channel {self.CHANNEL_ID} not open yet")
            return
        self.on_frame(channel_id, message_id, encrypted, body)

    # ------------------------------------------------------------------
    # Outgoing frame helper
    # ------------------------------------------------------------------

    def send_frame(self, message_id: int, proto_body: bytes, encrypted: bool = True) -> None:
        """Send an AA frame on this module's channel.

        Publishes on aa.frame.send:
            channel_id  : int   — this module's CHANNEL_ID
            message_id  : int   — 2-byte AA message identifier
            payload_hex : str   — proto body ONLY
            encrypted   : bool  — echoed from the received frame, or True by default post-handshake

        Args:
            message_id:  2-byte big-endian AA message identifier.
            proto_body:  serialised protobuf payload (may be empty bytes).
            encrypted:   echo from received frame's encrypted flag (default True).
        """
        self.log.debug(
            "send_frame: ch=%d msg=0x%04x enc=%s body_len=%d",
            self.CHANNEL_ID, message_id, encrypted, len(proto_body),
        )
        self.bus.publish("aa.frame.send", {
            "channel_id":  self.CHANNEL_ID,
            "message_id":  message_id,
            "payload_hex": proto_body.hex(),
            "encrypted":   encrypted,
        })

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
        """Called when the AA control channel opens this channel."""

    @abstractmethod
    def on_channel_close(self, channel_id: int) -> None:
        """Called when the AA control channel closes this channel."""

    @abstractmethod
    def on_frame(self, channel_id: int, message_id: int, encrypted: bool, data: bytes) -> None:
        """Called for every fully assembled, decrypted frame on this channel.

        Args:
            channel_id:  AA channel number.
            message_id:  2-byte AA message identifier.
            encrypted:   True if the wire frame was encrypted (echo of original flag).
            data:        decrypted proto body bytes.
        """

    # ------------------------------------------------------------------
    # Optional hooks
    # ------------------------------------------------------------------

    def _init(self) -> None:
        """One-time resource allocation. Called during channel_manager.module_start."""

    def _cleanup(self) -> None:
        """Release resources. Called on channel_manager.module_stop."""

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        schema = self.get_schema()
        if schema:
            self.cfg.on_config_loaded  = self.on_config_loaded
            self.cfg.on_config_changed = self.on_config_changed
            self.cfg.register()

        self.bus.subscribe("channel_manager.module_start",        self._on_channel_manager_module_start)
        self.bus.subscribe("channel_manager.module_stop",         self._on_channel_manager_module_stop)
        self.bus.subscribe("aa.channel.open",  self._on_aa_channel_open)
        self.bus.subscribe("aa.channel.close", self._on_aa_channel_close)

        frame_topic = f"aa.frame.ch{self.CHANNEL_ID}"
        self.bus.subscribe(frame_topic, self._on_aa_frame)
        self.log.info(f"Subscribed to {frame_topic}")

        self.log.info(
            f"{self.MODULE_NAME} started "
            f"(channel_id={self.CHANNEL_ID}, priority={self.PRIORITY}) "
            f"— waiting for messages..."
        )
        bus_thread = self.bus.start(blocking=False)
        time.sleep(0.05)
        self._on_channel_manager_module_readytostart()
        try:
            bus_thread.join()
        except KeyboardInterrupt:
            pass
