"""
NemoHeadUnit-Wireless v2 — channel_modules/input

Module contract:
  Name        : input  (overridden by --module-name)
  Priority    : 1
  Channel ID  : supplied via --channel-id CLI arg (parsed by BaseChannelModule)
  SDR bytes   : supplied via --sdr-bytes-hex CLI arg, parsed by base into
                self.channel_config.
  Subscribes  : channel_manager.module_readytostart
                channel_manager.module_start
                channel_manager.module_stop
                aa.channel.open          {channel_id, ...}
                aa.channel.close         {channel_id}
                aa.frame.ch<channel_id>    raw bytes  (ChannelOpenRequest, KeyBindingRequest)
                aa.session.active         {}
                aa.session.shutdown       {}
                input.touch               {action, pointers, action_index?, disp_channel_id?}
                input.key                 {keycode, down, metastate?, longpress?, disp_channel_id?}
  Publishes   : channel_manager.module_ready       {name, priority}
                channel_manager.module_ready              {name, priority}
                aa.frame.send             {channel_id, flags, payload_hex}  ← ChannelOpenResponse,
                                                                               KeyBindingResponse,
                                                                               InputReport
                input.state               {state}  IDLE | OPEN | BOUND

Flow:
  1. BaseChannelModule parses CLI and populates self.CHANNEL_ID and
     self.channel_config from --channel-id / --sdr-bytes-hex.
  2. channel_manager.module_ready is published lazily by base once _init_done, config_loaded
     and channel_config is not None.
  3. On aa.frame.ch<channel_id>:
       - ChannelOpenRequest   → reply ChannelOpenResponse (STATUS_SUCCESS)
       - KeyBindingRequest    → negotiate keycodes, reply KeyBindingResponse
  4. On input.touch / input.key (from UI layer): build InputReport and send
     via aa.frame.send.
  5. On aa.session.shutdown: reset to IDLE, clear channel reference.

Input report encoding:
  All outgoing AA frames use the minimal hand-rolled protobuf encoder
  (no proto dependency) for InputReport, TouchEvent, KeyEvent.
  The wire format mirrors aasdk_proto.service.inputsource exactly.

Default keycodes exposed (same list as NemoHeadUnit InputOrchestrator):
  HOME, BACK, CALL, ENDCALL, DPAD_*, ENTER, MENU,
  MEDIA_PLAY_PAUSE, MEDIA_NEXT, MEDIA_PREVIOUS,
  VOLUME_UP, VOLUME_DOWN, VOLUME_MUTE, VOICE_ASSIST.
"""

from __future__ import annotations

import struct
import sys
import time
from pathlib import Path
from typing import List, Tuple

_HERE         = Path(__file__).parent          # v2/modules/channel_modules/input/
_CHANNEL_MODS = _HERE.parent                   # v2/modules/channel_modules/
_MODULES      = _CHANNEL_MODS.parent           # v2/modules/
_V2           = _MODULES.parent                # v2/

for _p in (_V2, _MODULES, _CHANNEL_MODS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from channel_modules.base_channel_module import BaseChannelModule  # noqa: E402
from shared.proto_utils import encode_aa_frame, decode_aa_frame    # noqa: E402

# ---------------------------------------------------------------------------
# AA message IDs
# ---------------------------------------------------------------------------

_MSG_CHANNEL_OPEN_REQUEST   = 0x8003
_MSG_CHANNEL_OPEN_RESPONSE  = 0x8005

_MSG_KEY_BINDING_REQUEST    = 0x8009
_MSG_KEY_BINDING_RESPONSE   = 0x800A
_MSG_INPUT_REPORT           = 0x0001

# ---------------------------------------------------------------------------
# Keycodes (Android KeyEvent constants — wire values, no proto dependency)
# ---------------------------------------------------------------------------

KEYCODE_HOME            = 3
KEYCODE_BACK            = 4
KEYCODE_CALL            = 5
KEYCODE_ENDCALL         = 6
KEYCODE_DPAD_UP         = 19
KEYCODE_DPAD_DOWN       = 20
KEYCODE_DPAD_LEFT       = 21
KEYCODE_DPAD_RIGHT      = 22
KEYCODE_DPAD_CENTER     = 23
KEYCODE_ENTER           = 66
KEYCODE_MENU            = 82
KEYCODE_MEDIA_PLAY_PAUSE = 85
KEYCODE_MEDIA_NEXT      = 87
KEYCODE_MEDIA_PREVIOUS  = 88
KEYCODE_VOLUME_UP       = 24
KEYCODE_VOLUME_DOWN     = 25
KEYCODE_VOLUME_MUTE     = 164
KEYCODE_VOICE_ASSIST    = 231

_DEFAULT_KEYCODES: List[int] = [
    KEYCODE_HOME, KEYCODE_BACK, KEYCODE_CALL, KEYCODE_ENDCALL,
    KEYCODE_DPAD_UP, KEYCODE_DPAD_DOWN, KEYCODE_DPAD_LEFT,
    KEYCODE_DPAD_RIGHT, KEYCODE_DPAD_CENTER, KEYCODE_ENTER,
    KEYCODE_MENU, KEYCODE_MEDIA_PLAY_PAUSE, KEYCODE_MEDIA_NEXT,
    KEYCODE_MEDIA_PREVIOUS, KEYCODE_VOLUME_UP, KEYCODE_VOLUME_DOWN,
    KEYCODE_VOLUME_MUTE, KEYCODE_VOICE_ASSIST,
]

ACTION_DOWN   = 0
ACTION_UP     = 1
ACTION_MOVED  = 2


# ---------------------------------------------------------------------------
# InputModule
# ---------------------------------------------------------------------------

class InputModule(BaseChannelModule):
    """
    AA Input channel module.

    Receives ChannelOpenRequest and KeyBindingRequest from the phone,
    then relays InputReports (touch + key events) published on the bus
    by the UI layer.

    channel_id and SDR bytes are provided at spawn time via CLI by
    channel_manager and parsed by BaseChannelModule into self.CHANNEL_ID
    and self.channel_config.  channel_manager.module_ready is hard-blocked by base if
    channel_config is None.
    """

    MODULE_NAME = "input"   # overridden by --module-name CLI
    CHANNEL_ID  = -1         # overridden by --channel-id CLI
    PRIORITY    = 1

    def __init__(self) -> None:
        super().__init__()
        self._state            = "IDLE"   # IDLE | OPEN | BOUND
        self._bound_keycodes: set = set()
        self._channel_open_flag  = False

    # ------------------------------------------------------------------
    # _init / _cleanup hooks
    # ------------------------------------------------------------------

    def _init(self) -> None:
        self.log.info(
            "InputModule _init: channel_id=%d channel_config=%s",
            self.CHANNEL_ID,
            self.channel_config,
        )

    def _cleanup(self) -> None:
        self._set_state("IDLE")
        self._bound_keycodes.clear()
        self._channel_open_flag = False

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def on_aa_session_active(self, topic: str, payload: dict) -> None:
        self._bound_keycodes.clear()
        self._channel_open_flag = False
        self._set_state("IDLE")
        self.log.info("AA session active — input ready")

    def on_aa_session_shutdown(self, topic: str, payload: dict) -> None:
        self._bound_keycodes.clear()
        self._channel_open_flag = False
        self._set_state("IDLE")
        self.log.info("AA session shutdown — input reset")

    # ------------------------------------------------------------------
    # BaseChannelModule abstract interface
    # ------------------------------------------------------------------

    def on_channel_open(self, channel_id: int, descriptor: dict) -> None:
        self._channel_open_flag = True
        self.log.info("Input channel %d open (descriptor: %s)", channel_id, descriptor)

    def on_channel_close(self, channel_id: int) -> None:
        self._channel_open_flag = False
        self._set_state("IDLE")
        self.log.info("Input channel %d closed", channel_id)

    def on_frame(self, channel_id: int, data: bytes) -> None:
        """Dispatch incoming frame by AA message_id."""
        result = decode_aa_frame(data)
        if result is None:
            self.log.error("on_frame: malformed payload — dropping")
            return

        message_id, body = result

        if message_id == _MSG_CHANNEL_OPEN_REQUEST:
            self._handle_channel_open_request(body)
        elif message_id == _MSG_KEY_BINDING_REQUEST:
            self._handle_key_binding_request(body)
        else:
            self.log.debug(
                "Unhandled input msg_id=0x%04x len=%d", message_id, len(body)
            )

    # ------------------------------------------------------------------
    # Incoming message handlers
    # ------------------------------------------------------------------

    def _handle_channel_open_request(self, body: bytes) -> None:
        frame = encode_aa_frame(self.CHANNEL_ID, _MSG_CHANNEL_OPEN_RESPONSE, b"\x08\x00")
        self.bus.publish("aa.frame.send", frame)
        self._set_state("OPEN")
        self.log.info("ChannelOpenRequest → ChannelOpenResponse sent (STATUS_SUCCESS)")

    def _handle_key_binding_request(self, body: bytes) -> None:
        requested = _parse_key_binding_request(body)
        supported = set(_DEFAULT_KEYCODES)

        if requested:
            accepted = requested & supported
            rejected = requested - supported
            self._bound_keycodes = accepted
            if rejected:
                self.log.warning(
                    "KeyBindingRequest: %d unsupported keycode(s) rejected: %s",
                    len(rejected), sorted(rejected),
                )
        else:
            self._bound_keycodes = supported

        frame = encode_aa_frame(self.CHANNEL_ID, _MSG_KEY_BINDING_RESPONSE, b"\x08\x00")
        self.bus.publish("aa.frame.send", frame)
        self._set_state("BOUND")
        self.log.info(
            "KeyBindingRequest → KeyBindingResponse sent (%d keycodes bound)",
            len(self._bound_keycodes),
        )

    # ------------------------------------------------------------------
    # Outgoing input reports
    # ------------------------------------------------------------------

    def on_input_touch(self, topic: str, payload: dict) -> None:
        if self._state not in ("OPEN", "BOUND"):
            self.log.debug("on_input_touch: channel not open — dropping")
            return
        action          = int(payload.get("action", ACTION_DOWN))
        raw_pointers    = payload.get("pointers", [])
        action_index    = int(payload.get("action_index", 0))
        disp_channel_id = int(payload.get("disp_channel_id", 0))
        pointers: List[Tuple[int, int, int]] = [
            (int(p[0]), int(p[1]), int(p[2])) for p in raw_pointers
        ]
        if not pointers:
            self.log.warning("on_input_touch: empty pointers list — dropping")
            return
        report_bytes = _build_input_report_touch(action, pointers, action_index, disp_channel_id)
        frame = encode_aa_frame(self.CHANNEL_ID, _MSG_INPUT_REPORT, report_bytes)
        self.bus.publish("aa.frame.send", frame)
        self.log.debug("TouchEvent sent action=%d pointers=%s", action, pointers)

    def on_input_key(self, topic: str, payload: dict) -> None:
        if self._state not in ("OPEN", "BOUND"):
            self.log.debug("on_input_key: channel not open — dropping")
            return
        keycode         = int(payload.get("keycode", 0))
        down            = bool(payload.get("down", True))
        metastate       = int(payload.get("metastate", 0))
        longpress       = bool(payload.get("longpress", False))
        disp_channel_id = int(payload.get("disp_channel_id", 0))
        if self._bound_keycodes and keycode not in self._bound_keycodes:
            self.log.debug("on_input_key: keycode=%d not bound — dropping", keycode)
            return
        report_bytes = _build_input_report_key(keycode, down, metastate, longpress, disp_channel_id)
        frame = encode_aa_frame(self.CHANNEL_ID, _MSG_INPUT_REPORT, report_bytes)
        self.bus.publish("aa.frame.send", frame)
        self.log.debug("KeyEvent sent keycode=%d down=%s", keycode, down)

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def send_touch_down(self, x: int, y: int, pointer_id: int = 0, disp_channel_id: int = 0) -> None:
        self.on_input_touch("", {"action": ACTION_DOWN, "pointers": [[x, y, pointer_id]], "disp_channel_id": disp_channel_id})

    def send_touch_up(self, x: int, y: int, pointer_id: int = 0, disp_channel_id: int = 0) -> None:
        self.on_input_touch("", {"action": ACTION_UP, "pointers": [[x, y, pointer_id]], "disp_channel_id": disp_channel_id})

    def send_touch_move(self, x: int, y: int, pointer_id: int = 0, disp_channel_id: int = 0) -> None:
        self.on_input_touch("", {"action": ACTION_MOVED, "pointers": [[x, y, pointer_id]], "disp_channel_id": disp_channel_id})

    def send_key_down(self, keycode: int, metastate: int = 0, disp_channel_id: int = 0) -> None:
        self.on_input_key("", {"keycode": keycode, "down": True, "metastate": metastate, "disp_channel_id": disp_channel_id})

    def send_key_up(self, keycode: int, metastate: int = 0, disp_channel_id: int = 0) -> None:
        self.on_input_key("", {"keycode": keycode, "down": False, "metastate": metastate, "disp_channel_id": disp_channel_id})

    # ------------------------------------------------------------------
    # State helper
    # ------------------------------------------------------------------

    def _set_state(self, new_state: str) -> None:
        self._state = new_state
        self.bus.publish("input.state", {"state": new_state})
        self.log.info("input.state → %s", new_state)

    # ------------------------------------------------------------------
    # run() override
    # ------------------------------------------------------------------

    def run(self) -> None:
        self.bus.subscribe("aa.session.active",   self.on_aa_session_active)
        self.bus.subscribe("aa.session.shutdown", self.on_aa_session_shutdown)
        self.bus.subscribe("input.touch",         self.on_input_touch)
        self.bus.subscribe("input.key",           self.on_input_key)
        super().run()


# ---------------------------------------------------------------------------
# Minimal hand-rolled protobuf helpers  (no proto dependency)
# ---------------------------------------------------------------------------

def _read_varint(buf: bytes, pos: int) -> tuple[int | None, int]:
    result = 0
    shift  = 0
    while pos < len(buf):
        b = buf[pos]; pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
        if shift >= 64:
            return None, pos
    return None, pos


def _encode_varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    out = []
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _field(field_number: int, wire_type: int, value_bytes: bytes) -> bytes:
    tag = (field_number << 3) | wire_type
    return _encode_varint(tag) + value_bytes


def _varint_field(field_number: int, value: int) -> bytes:
    return _field(field_number, 0, _encode_varint(value))


def _bytes_field(field_number: int, data: bytes) -> bytes:
    return _field(field_number, 2, _encode_varint(len(data)) + data)


def _bool_field(field_number: int, value: bool) -> bytes:
    return _varint_field(field_number, 1 if value else 0)


def _now_us() -> int:
    return int(time.time_ns() // 1000)


def _build_touch_event(action: int, pointers: List[Tuple[int, int, int]], action_index: int = 0) -> bytes:
    body = _varint_field(1, action) + _varint_field(2, action_index)
    for x, y, pid in pointers:
        pd = _varint_field(1, x) + _varint_field(2, y) + _varint_field(3, pid)
        body += _bytes_field(3, pd)
    return body


def _build_key_event(keycode: int, down: bool, metastate: int = 0, longpress: bool = False) -> bytes:
    key = (
        _varint_field(1, keycode)
        + _bool_field(2, down)
        + _varint_field(3, metastate)
        + _bool_field(4, longpress)
    )
    return _bytes_field(1, key)


def _build_input_report_touch(action: int, pointers: List[Tuple[int, int, int]], action_index: int = 0, disp_channel_id: int = 0) -> bytes:
    ts    = _now_us()
    touch = _build_touch_event(action, pointers, action_index)
    body  = _field(1, 1, struct.pack("<Q", ts))
    body += _bytes_field(2, touch)
    if disp_channel_id:
        body += _varint_field(5, disp_channel_id)
    return body


def _build_input_report_key(keycode: int, down: bool, metastate: int = 0, longpress: bool = False, disp_channel_id: int = 0) -> bytes:
    ts  = _now_us()
    key = _build_key_event(keycode, down, metastate, longpress)
    body  = _field(1, 1, struct.pack("<Q", ts))
    body += _bytes_field(3, key)
    if disp_channel_id:
        body += _varint_field(5, disp_channel_id)
    return body


def _parse_key_binding_request(body: bytes) -> set:
    result = set()
    pos = 0
    while pos < len(body):
        tag_byte     = body[pos]; pos += 1
        field_number = tag_byte >> 3
        wire_type    = tag_byte & 0x07
        if field_number == 1 and wire_type == 0:
            val, pos = _read_varint(body, pos)
            if val is not None:
                result.add(val)
        else:
            if wire_type == 0:
                _, pos = _read_varint(body, pos)
            elif wire_type == 1:
                pos += 8
            elif wire_type == 2:
                length, pos = _read_varint(body, pos)
                if length:
                    pos += length
            elif wire_type == 5:
                pos += 4
            else:
                break
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    InputModule().run()
