"""
NemoHeadUnit-Wireless v2 — channel_modules/bluetooth

Module contract:
  Name        : bluetooth
  Priority    : 1
  Channel ID  : parsed by BaseChannelModule from --channel-id CLI
  SDR bytes   : parsed by base into self.channel_config

  Subscribes  : channel_manager.module_start
                channel_manager.module_stop
                aa.channel.open     {channel_id, ...}
                aa.channel.close    {channel_id}
                aa.frame.ch<ch_id>  raw bytes

  Publishes   : channel_manager.module_ready  {name, priority}
                bluetooth.pairing_request      {phone_address, phone_name, pairing_method}
                bluetooth.auth_data            {data_hex}
                bluetooth.auth_result          {data_hex}

  Config keys : (none — no external resource, no configurable parameters)

---
AA Bluetooth channel — NON-AV channel:
  This channel does NOT use the AV handshake flow (no AVChannelSetupRequest,
  no session_id, no media ACK).  on_frame() dispatches directly by message_id
  using BluetoothChannelMessage IDs.

Message flow:
  PAIRING_REQUEST  → parse BluetoothPairingRequest
                   → publish bluetooth.pairing_request
                   → respond PAIRING_RESPONSE (already_paired=True, status=0)

  AUTH_DATA        → forward raw bytes on bluetooth.auth_data (not processed here)
  AUTH_RESULT      → forward raw bytes on bluetooth.auth_result (not processed here)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# sys.path bootstrap
# ---------------------------------------------------------------------------
_HERE         = Path(__file__).parent          # v2/modules/channel_modules/bluetooth/
_CHANNEL_MODS = _HERE.parent                   # v2/modules/channel_modules/
_MODULES      = _CHANNEL_MODS.parent           # v2/modules/
_V2           = _MODULES.parent                # v2/
_PROTOS       = _V2 / "protos"                 # v2/protos/

for _p in (_V2, _MODULES, _CHANNEL_MODS, _PROTOS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------
from shared.proto_utils import decode_aa_frame                              # noqa: E402
from channel_modules.base_channel_module import BaseChannelModule           # noqa: E402

# ---------------------------------------------------------------------------
# Proto imports — Bluetooth channel
# ---------------------------------------------------------------------------
from oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage                       # noqa: E402
from oaa.control.ChannelOpenResponseMessage_pb2 import ChannelOpenResponse             # noqa: E402
from oaa.common.StatusEnum_pb2 import Status                                           # noqa: E402
from oaa.bluetooth.BluetoothChannelMessageIdsEnum_pb2 import BluetoothChannelMessage   # noqa: E402
from oaa.bluetooth.BluetoothPairingRequestMessage_pb2 import BluetoothPairingRequest   # noqa: E402
from oaa.bluetooth.BluetoothPairingResponseMessage_pb2 import BluetoothPairingResponse # noqa: E402

# ---------------------------------------------------------------------------
# AA message ID aliases
# ---------------------------------------------------------------------------
_MSG_CHANNEL_OPEN_REQUEST       = ControlMessage.CHANNEL_OPEN_REQUEST
_MSG_CHANNEL_OPEN_RESPONSE      = ControlMessage.CHANNEL_OPEN_RESPONSE
_MSG_PAIRING_REQUEST  = BluetoothChannelMessage.Enum.PAIRING_REQUEST
_MSG_PAIRING_RESPONSE = BluetoothChannelMessage.Enum.PAIRING_RESPONSE
_MSG_AUTH_DATA        = BluetoothChannelMessage.Enum.AUTH_DATA
_MSG_AUTH_RESULT      = BluetoothChannelMessage.Enum.AUTH_RESULT


# ---------------------------------------------------------------------------
# BluetoothModule
# ---------------------------------------------------------------------------

class BluetoothModule(BaseChannelModule):
    """
    AA Bluetooth channel module.

    Non-AV channel: bypasses the entire AV handshake flow.
    on_frame() dispatches directly by BluetoothChannelMessage ID.

    Pairing strategy: always respond already_paired=True, status=0.
    BT pairing is handled externally (bluetoothctl / rfcomm layer).

    AUTH_DATA and AUTH_RESULT are forwarded on the bus but not processed here.
    """

    MODULE_NAME: str = "bluetooth"
    CHANNEL_ID:  int = -1   # always overridden by --channel-id
    PRIORITY:    int = 1

    # ------------------------------------------------------------------
    # Config schema — no configurable parameters for this module
    # ------------------------------------------------------------------

    def get_schema(self) -> dict:
        return {}

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        super().__init__()

    # ------------------------------------------------------------------
    # BaseChannelModule abstract interface
    # ------------------------------------------------------------------

    def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
        self.log.info("Bluetooth channel %d open (descriptor: %s)", channel_id, descriptor)

    def on_channel_close(self, channel_id: int) -> None:
        self.log.info("Bluetooth channel %d closed", channel_id)

    def on_frame(self, channel_id: int, data: bytes) -> None:
        """
        Entry point for every raw binary frame on the Bluetooth channel.

        This is a NON-AV channel: no AVChannelSetupRequest, no session_id,
        no media ACK.  Decode the frame header and dispatch by message_id
        using BluetoothChannelMessage IDs directly.
        """
        result = decode_aa_frame(data)
        if result is None:
            self.log.error(
                "on_frame: malformed payload on ch=%d — dropping", channel_id
            )
            return

        message_id, body = result

        if message_id == _MSG_PAIRING_REQUEST:
            self._handle_pairing_request(body)
        elif message_id == _MSG_CHANNEL_OPEN_REQUEST:
            self._handle_open_request(body)
        elif message_id == _MSG_AUTH_DATA:
            self._handle_auth_data(body)
        elif message_id == _MSG_AUTH_RESULT:
            self._handle_auth_result(body)
        else:
            self.log.debug(
                "Unhandled msg_id=0x%04x ch=%d len=%d",
                message_id, channel_id, len(body),
            )

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _handle_open_request(self, body: bytes) -> None:
        """Send ChannelOpenResponse and transition to OPEN."""
        resp = ChannelOpenResponse()
        resp.status = Status.OK
        self.send_frame(_MSG_CHANNEL_OPEN_RESPONSE, resp.SerializeToString())
        self.log.info("ChannelOpenRequest ch=%d → ChannelOpenResponse sent", self.CHANNEL_ID)

    def _handle_pairing_request(self, body: bytes) -> None:
        """
        Parse BluetoothPairingRequest, publish on the bus, then respond
        with already_paired=True, status=0 (SUCCESS).

        BT pairing is managed externally — this module never touches bluetoothctl.
        """
        try:
            req = BluetoothPairingRequest()
            req.ParseFromString(body)
        except Exception as exc:
            self.log.warning(
                "PAIRING_REQUEST parse error ch=%d — %s — sending already_paired anyway",
                self.CHANNEL_ID, exc,
            )
            req = None

        phone_address = req.phone_address if req and req.HasField("phone_address") else "unknown"
        phone_name    = req.phone_name    if req and req.HasField("phone_name")    else ""
        pairing_method = int(req.pairing_method) if req and req.HasField("pairing_method") else 0

        self.log.info(
            "PAIRING_REQUEST ch=%d phone_address=%s phone_name=%r method=%d",
            self.CHANNEL_ID, phone_address, phone_name, pairing_method,
        )

        # Forward to bus — other modules (e.g. rfcomm_handshake) may act on it
        self.bus.publish("bluetooth.pairing_request", {
            "phone_address":  phone_address,
            "phone_name":     phone_name,
            "pairing_method": pairing_method,
        })

        # Respond: already paired — no local BT action needed
        resp = BluetoothPairingResponse()
        resp.already_paired = True
        resp.status = 0  # SUCCESS
        self.send_frame(_MSG_PAIRING_RESPONSE, resp.SerializeToString())
        self.log.info(
            "PAIRING_RESPONSE sent ch=%d already_paired=True status=0",
            self.CHANNEL_ID,
        )

    def _handle_auth_data(self, body: bytes) -> None:
        """Forward AUTH_DATA raw bytes on the bus. Not processed in this module."""
        self.log.debug(
            "AUTH_DATA ch=%d len=%d hex_prefix=%s",
            self.CHANNEL_ID, len(body), body[:16].hex(),
        )
        self.bus.publish("bluetooth.auth_data", {"data_hex": body.hex()})

    def _handle_auth_result(self, body: bytes) -> None:
        """Forward AUTH_RESULT raw bytes on the bus. Not processed in this module."""
        self.log.debug(
            "AUTH_RESULT ch=%d len=%d hex_prefix=%s",
            self.CHANNEL_ID, len(body), body[:16].hex(),
        )
        self.bus.publish("bluetooth.auth_result", {"data_hex": body.hex()})

    # ------------------------------------------------------------------
    # run() override — no extra subscriptions needed for this module
    # ------------------------------------------------------------------

    def run(self) -> None:
        super().run()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    BluetoothModule().run()
