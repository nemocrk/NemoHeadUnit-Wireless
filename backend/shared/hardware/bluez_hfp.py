"""
bluez_hfp.py — Standalone Bluetooth Hands-Free Profile (HFP) AT command & call management.
Handles dial, answer, hangup, mute, DTMF tones, and call state tracking.
"""

import enum
import re
from typing import Optional, Dict, Any, Callable
from shared.logger import get_logger

log = get_logger("hardware.bluez_hfp")


class CallState(str, enum.Enum):
    IDLE = "IDLE"
    DIALING = "DIALING"
    RINGING = "RINGING"
    ACTIVE = "ACTIVE"
    HELD = "HELD"


def format_at_dial(number: str) -> str:
    # Ensure semicolon at end for voice call dial command in GSM 07.07 / 3GPP 27.007
    num = number.strip()
    return f"ATD{num};\r"


def format_at_answer() -> str:
    return "ATA\r"


def format_at_hangup() -> str:
    return "AT+CHUP\r"


def format_at_dtmf(key: str) -> str:
    return f"AT+VTS={key}\r"


def format_at_mute(muted: bool) -> str:
    return f"AT+CMUT={1 if muted else 0}\r"


def parse_clcc_response(line: str) -> Optional[Dict[str, Any]]:
    """
    Parse a 3GPP +CLCC line:
    +CLCC: <idx>,<dir>,<stat>,<mode>,<mpty>[,"<number>",<type>]
    stat: 0=active, 1=held, 2=dialing, 3=alerting, 4=incoming, 5=waiting
    dir: 0=outgoing, 1=incoming
    """
    line = line.strip()
    match = re.match(r'^\+CLCC:\s*(\d+),(\d+),(\d+),(\d+),(\d+)(?:,"([^"]*)",(\d+))?', line)
    if not match:
        return None

    idx = int(match.group(1))
    direction = "incoming" if int(match.group(2)) == 1 else "outgoing"
    stat_code = int(match.group(3))
    number = match.group(6) or ""

    state_map = {
        0: CallState.ACTIVE,
        1: CallState.HELD,
        2: CallState.DIALING,
        3: CallState.DIALING,  # Alerting remote
        4: CallState.RINGING,  # Incoming ringing
        5: CallState.HELD,
    }
    state = state_map.get(stat_code, CallState.IDLE)

    return {
        "idx": idx,
        "direction": direction,
        "state": state,
        "number": number,
    }


def parse_ciev_response(line: str, ind_map: Dict[int, str]) -> Optional[Dict[str, Any]]:
    """
    Parse a standard +CIEV indicator event:
    +CIEV: <ind>,<val>
    """
    line = line.strip()
    match = re.match(r'^\+CIEV:\s*(\d+),(\d+)', line)
    if not match:
        return None

    ind_idx = int(match.group(1))
    val = int(match.group(2))
    name = ind_map.get(ind_idx, f"indicator_{ind_idx}")

    return {
        "indicator": name,
        "value": val,
    }


class BlueZHFClient:
    """
    Hands-Free Client managing AT command sequence, call lifecycle, and audio loopback sync.
    Can operate directly with an open RFCOMM socket/file descriptor or in mock/standalone mode.
    """

    def __init__(
        self,
        rfcomm_fd: Optional[int] = None,
        on_state_changed: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self._rfcomm_fd = rfcomm_fd
        self._on_state_changed = on_state_changed
        self._call_state: CallState = CallState.IDLE
        self._phone_number: str = ""
        self._caller_name: str = ""
        self._muted: bool = False
        self._carrier: str = ""
        self._signal_bars: int = 4
        self._battery_pct: int = 85
        self._is_roaming: bool = False

    def get_state(self) -> Dict[str, Any]:
        return {
            "source": "bluetooth_hfp",
            "is_in_call": self._call_state != CallState.IDLE,
            "call_state": self._call_state.value,
            "phone_number": self._phone_number,
            "caller_name": self._caller_name,
            "muted": self._muted,
            "carrier": self._carrier,
            "signal_bars": self._signal_bars,
            "battery_pct": self._battery_pct,
            "is_roaming": self._is_roaming,
        }

    def _notify(self) -> None:
        if self._on_state_changed:
            try:
                self._on_state_changed(self.get_state())
            except Exception as e:
                log.warning(f"Error in on_state_changed callback: {e}")

    def send_at_cmd(self, cmd: str) -> bool:
        log.info(f"📤 HFP Sending AT Command: {repr(cmd)}")
        if self._rfcomm_fd is not None:
            try:
                import os
                os.write(self._rfcomm_fd, cmd.encode("ascii"))
                return True
            except Exception as e:
                log.warning(f"Failed to write AT command to RFCOMM fd: {e}")
                return False
        return True

    def dial(self, number: str) -> bool:
        clean_num = number.strip()
        if not clean_num:
            return False
        self._phone_number = clean_num
        self._call_state = CallState.DIALING
        self._muted = False
        cmd = format_at_dial(clean_num)
        sent = self.send_at_cmd(cmd)
        self._notify()
        return sent

    def answer(self) -> bool:
        if self._call_state != CallState.RINGING:
            log.debug(f"answer called but call_state is {self._call_state}")
        self._call_state = CallState.ACTIVE
        cmd = format_at_answer()
        sent = self.send_at_cmd(cmd)
        self._notify()
        return sent

    def hangup(self) -> bool:
        self._call_state = CallState.IDLE
        self._phone_number = ""
        self._caller_name = ""
        self._muted = False
        cmd = format_at_hangup()
        sent = self.send_at_cmd(cmd)
        self._notify()
        return sent

    def send_dtmf(self, key: str) -> bool:
        cmd = format_at_dtmf(key)
        return self.send_at_cmd(cmd)

    def set_mute(self, muted: bool) -> bool:
        self._muted = muted
        cmd = format_at_mute(muted)
        sent = self.send_at_cmd(cmd)
        self._notify()
        return sent

    def handle_call_active(self) -> None:
        self._call_state = CallState.ACTIVE
        self._notify()

    def handle_incoming_call(self, number: str, name: str = "") -> None:
        self._phone_number = number
        self._caller_name = name or number
        self._call_state = CallState.RINGING
        self._notify()

    def update_telemetry(self, battery_pct: int = -1, signal_bars: int = -1, carrier: str = "", is_roaming: Optional[bool] = None) -> None:
        if battery_pct >= 0:
            self._battery_pct = max(0, min(100, battery_pct))
        if signal_bars >= 0:
            self._signal_bars = max(0, min(5, signal_bars))
        if carrier:
            self._carrier = carrier
        if is_roaming is not None:
            self._is_roaming = is_roaming
        self._notify()
