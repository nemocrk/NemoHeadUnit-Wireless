#!/usr/bin/env python3
"""
test_hfp_at_commands.py — Real-Time Bluetooth HFP AT Command Monitor & Diagnostic Tool.

Captures, decodes, and logs all Bluetooth Hands-Free Profile (HFP) AT commands
exchanged between the head unit (HF) and the connected smartphone (AG) via btmon,
and provides CLI shortcuts to trigger dial, answer, hangup, dtmf, and PBAP sync.

Usage:
    # Live monitor and decode all HFP AT commands in real time:
    sudo python3 scripts/hardware_tests/test_hfp_at_commands.py --monitor

    # Live monitor and log to file:
    sudo python3 scripts/hardware_tests/test_hfp_at_commands.py --monitor --log /tmp/hfp_at.log

    # Query current phone telemetry & call state:
    python3 scripts/hardware_tests/test_hfp_at_commands.py --status

    # Trigger a test call or call action via backend:
    python3 scripts/hardware_tests/test_hfp_at_commands.py --dial "112"
    python3 scripts/hardware_tests/test_hfp_at_commands.py --hangup
    python3 scripts/hardware_tests/test_hfp_at_commands.py --answer
    python3 scripts/hardware_tests/test_hfp_at_commands.py --sync
"""

import argparse
import datetime
import json
import os
import re
import select
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

# ANSI Color codes
CLR_RESET = "\033[0m"
CLR_BOLD = "\033[1m"
CLR_RED = "\033[1;31m"
CLR_GREEN = "\033[1;32m"
CLR_YELLOW = "\033[1;33m"
CLR_BLUE = "\033[1;34m"
CLR_MAGENTA = "\033[1;35m"
CLR_CYAN = "\033[1;36m"
CLR_GRAY = "\033[0;90m"

# HFP Standard indicator index mapping (from +CIND)
INDICATOR_NAMES = {
    1: "service",
    2: "call",
    3: "callsetup",
    4: "callheld",
    5: "signal",
    6: "roam",
    7: "battchg",
}

CALLSETUP_VALUES = {
    0: "None (Idle)",
    1: "Incoming call alerting",
    2: "Outgoing call dialing",
    3: "Outgoing call alerting (remote ringing)",
}

CALL_VALUES = {
    0: "No active calls",
    1: "Call in progress",
}

CODEC_NAMES = {
    1: "CVSD (Standard 8kHz Narrowband)",
    2: "mSBC (Wideband Speech 16kHz HD Voice)",
    3: "LC3-SWB (Super Wideband)",
}


def explain_at_command(cmd: str, is_outbound: bool) -> str:
    """Return human-readable explanation of HFP AT command or response."""
    clean = cmd.strip()
    if not clean:
        return ""

    if is_outbound:
        # Commands sent from Head Unit (HF) to Phone (AG)
        if clean.startswith("ATD"):
            num = clean[3:].rstrip(";")
            return f"{CLR_YELLOW}Dial request for: {num}{CLR_RESET}"
        if clean == "ATA":
            return f"{CLR_GREEN}Answer incoming call{CLR_RESET}"
        if clean in ("AT+CHUP", "ATH"):
            return f"{CLR_RED}Hang up / Terminate call{CLR_RESET}"
        if clean.startswith("AT+VTS="):
            digit = clean[7:]
            return f"{CLR_CYAN}Send DTMF tone: '{digit}'{CLR_RESET}"
        if clean.startswith("AT+BRSF="):
            return f"{CLR_BLUE}Features negotiation (BRSF bitmask: {clean[8:]}){CLR_RESET}"
        if clean == "AT+CIND=?":
            return f"{CLR_BLUE}Query supported indicators and ranges{CLR_RESET}"
        if clean == "AT+CIND?":
            return f"{CLR_BLUE}Query current indicator status{CLR_RESET}"
        if clean.startswith("AT+CMER="):
            return f"{CLR_BLUE}Enable unsolicited event reporting (indicators){CLR_RESET}"
        if clean.startswith("AT+CLIP="):
            return f"{CLR_BLUE}Enable Calling Line Identification (Caller ID){CLR_RESET}"
        if clean == "AT+CLCC":
            return f"{CLR_BLUE}Query active call list (+CLCC){CLR_RESET}"
        if clean.startswith("AT+BAC="):
            return f"{CLR_BLUE}Announce supported codecs (BAC: {clean[7:]}){CLR_RESET}"
        if clean.startswith("AT+BCS="):
            codec_id = int(clean[7:]) if clean[7:].isdigit() else -1
            codec_name = CODEC_NAMES.get(codec_id, f"Unknown ({codec_id})")
            return f"{CLR_GREEN}Confirm codec selection: {codec_name}{CLR_RESET}"
        if clean.startswith("AT+VGS="):
            return f"{CLR_CYAN}Set speaker volume: {clean[7:]}{CLR_RESET}"
        if clean.startswith("AT+VGM="):
            return f"{CLR_CYAN}Set mic volume: {clean[7:]}{CLR_RESET}"
        if clean.startswith("AT+XAPL="):
            return f"{CLR_BLUE}Apple iOS features announcement (battery/Siri){CLR_RESET}"
        if clean.startswith("AT+IPHONEACCEV="):
            return f"{CLR_BLUE}iOS accessory state report (battery level){CLR_RESET}"
        return f"{CLR_GRAY}HF Command{CLR_RESET}"
    else:
        # Responses / events sent from Phone (AG) to Head Unit (HF)
        if clean == "RING":
            return f"{CLR_RED}🔔 Incoming Call Ringing!{CLR_RESET}"
        if clean.startswith("+CLIP:"):
            match = re.search(r'\+CLIP:\s*"([^"]+)"', clean)
            caller = match.group(1) if match else clean
            return f"{CLR_YELLOW}Caller ID: {caller}{CLR_RESET}"
        if clean.startswith("+CIEV:"):
            # Indicator event: +CIEV: <ind_index>,<value>
            m = re.match(r'\+CIEV:\s*(\d+)\s*,\s*(\d+)', clean)
            if m:
                idx, val = int(m.group(1)), int(m.group(2))
                name = INDICATOR_NAMES.get(idx, f"ind_{idx}")
                extra = ""
                if name == "call":
                    extra = f" ({CALL_VALUES.get(val, '')})"
                elif name == "callsetup":
                    extra = f" ({CALLSETUP_VALUES.get(val, '')})"
                elif name == "battchg":
                    extra = f" (Battery ~{val * 20}%)"
                elif name == "signal":
                    extra = f" (Signal ~{val * 20}%)"
                return f"{CLR_MAGENTA}Indicator Event: {name}={val}{extra}{CLR_RESET}"
            return f"{CLR_MAGENTA}Indicator Event: {clean}{CLR_RESET}"
        if clean.startswith("+CLCC:"):
            return f"{CLR_YELLOW}Active Call Info: {clean}{CLR_RESET}"
        if clean.startswith("+BCS:"):
            codec_id = int(clean[5:]) if clean[5:].strip().isdigit() else -1
            codec_name = CODEC_NAMES.get(codec_id, f"Unknown ({codec_id})")
            return f"{CLR_GREEN}Phone proposed audio codec: {codec_name}{CLR_RESET}"
        if clean.startswith("+BRSF:"):
            return f"{CLR_BLUE}Phone supported features: {clean[6:]}{CLR_RESET}"
        if clean.startswith("+CIND:"):
            return f"{CLR_BLUE}Phone indicators response: {clean[6:]}{CLR_RESET}"
        if clean == "OK":
            return f"{CLR_GREEN}Command accepted (OK){CLR_RESET}"
        if clean.startswith("ERROR"):
            return f"{CLR_RED}Command rejected (ERROR){CLR_RESET}"
        return f"{CLR_GRAY}AG Response{CLR_RESET}"


def run_btmon_monitor(log_file: Optional[Path] = None, raw_mode: bool = False):
    """Monitor live btmon output and extract, highlight, and log all AT commands."""
    print(f"\n{CLR_BOLD}{CLR_CYAN}=== NemoHeadUnit Bluetooth HFP AT Command Monitor ==={CLR_RESET}")
    print(f"{CLR_GRAY}Listening for RFCOMM / AT frames across HCI controller... Press Ctrl+C to stop.{CLR_RESET}\n")

    cmd = ["btmon", "-i", "hci0"]
    if os.geteuid() != 0:
        cmd = ["sudo"] + cmd

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as e:
        print(f"{CLR_RED}Error starting btmon: {e}{CLR_RESET}")
        print(f"Please run with root privileges: sudo python3 {sys.argv[0]} --monitor")
        return

    log_handle = None
    if log_file:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_handle = open(log_file, "a", encoding="utf-8")
            print(f"{CLR_GREEN}Logging all AT commands to: {log_file}{CLR_RESET}\n")
        except Exception as e:
            print(f"{CLR_RED}Failed to open log file {log_file}: {e}{CLR_RESET}")

    at_pattern = re.compile(
        r'(AT[A-Z0-9\+\=\?\;\:\,\.\s]+|\+C[A-Z0-9\+\=\?\;\:\,\.\s]+|OK|ERROR|RING)',
        re.IGNORECASE,
    )

    try:
        for raw_line in iter(proc.stdout.readline, ''):
            line = raw_line.strip()
            if raw_mode:
                print(f"{CLR_GRAY}[RAW] {line}{CLR_RESET}")

            # Check for RFCOMM data payload or AT command signature in btmon output
            is_rfcomm = "RFCOMM" in line or "AT" in line or "RING" in line or "+C" in line
            if not is_rfcomm and not any(k in line for k in ("+CIEV", "+CLIP", "+CLCC", "+BCS", "+BRSF")):
                continue

            # Determine direction: > is outbound (HF -> AG), < is inbound (AG -> HF)
            is_outbound = line.startswith(">") or "ACL Data TX" in line
            is_inbound = line.startswith("<") or "ACL Data RX" in line

            # Match AT command content
            matches = at_pattern.findall(line)
            for match in matches:
                clean_cmd = match.strip()
                # Filter out false positives / noise
                if len(clean_cmd) < 2 or clean_cmd.isdigit() or clean_cmd.startswith("0x"):
                    continue
                if clean_cmd.upper() in ("AT", "TO", "ON"):
                    continue

                ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                dir_label = f"{CLR_GREEN}📤 HF  >> AG{CLR_RESET}" if is_outbound else f"{CLR_CYAN}📥 AG  << HF{CLR_RESET}"
                explanation = explain_at_command(clean_cmd, is_outbound)

                out_str = f"[{CLR_GRAY}{ts}{CLR_RESET}] {dir_label}  {CLR_BOLD}{clean_cmd:<24}{CLR_RESET}  {explanation}"
                print(out_str)

                if log_handle:
                    log_line = f"[{ts}] {'HF->AG' if is_outbound else 'AG->HF'} {clean_cmd} | {explanation}\n"
                    log_handle.write(log_line)
                    log_handle.flush()

    except KeyboardInterrupt:
        print(f"\n{CLR_YELLOW}Stopping Bluetooth AT command monitor.{CLR_RESET}")
    finally:
        proc.terminate()
        if log_handle:
            log_handle.close()


def send_backend_phone_request(endpoint: str, payload: Optional[dict] = None) -> dict:
    """Send request to local head unit connectivity backend."""
    url = f"http://127.0.0.1:8000/api/connectivity/phone/{endpoint}"
    data_bytes = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes if payload is not None else None,
        headers={"Content-Type": "application/json"} if payload is not None else {},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"status": "error", "message": str(e)}


def query_status():
    """Print current phone state, indicators, and call status."""
    print(f"\n{CLR_BOLD}{CLR_CYAN}=== Querying Phone & HFP Telemetry ==={CLR_RESET}")
    res = send_backend_phone_request("status")
    print(json.dumps(res, indent=2))

    print(f"\n{CLR_BOLD}{CLR_CYAN}=== Synced Contacts Count ==={CLR_RESET}")
    contacts_res = send_backend_phone_request("contacts")
    if contacts_res.get("status") == "ok":
        c_list = contacts_res.get("contacts", [])
        print(f"Total contacts: {len(c_list)}")
        if c_list:
            print(f"Sample contact: {c_list[0].get('name')} ({c_list[0].get('primary_phone')})")
    else:
        print(contacts_res)


def main():
    parser = argparse.ArgumentParser(
        description="Bluetooth HFP AT Command Diagnostic & Monitor Utility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-m", "--monitor", action="store_true", help="Start real-time btmon HFP AT command monitor")
    parser.add_argument("-l", "--log", type=Path, help="File path to save AT command log")
    parser.add_argument("--raw", action="store_true", help="Print raw btmon RFCOMM lines alongside decoded AT frames")
    parser.add_argument("-s", "--status", action="store_true", help="Query current phone and call status from backend")
    parser.add_argument("--dial", type=str, metavar="NUMBER", help="Dial phone number via backend")
    parser.add_argument("--hangup", action="store_true", help="Send hangup / end call action via backend")
    parser.add_argument("--answer", action="store_true", help="Send answer call action via backend")
    parser.add_argument("--dtmf", type=str, metavar="KEY", help="Send DTMF key (0-9, *, #) via backend")
    parser.add_argument("--sync", action="store_true", help="Trigger Bluetooth PBAP phonebook sync")

    args = parser.parse_args()

    if args.status:
        query_status()
        return

    if args.dial:
        print(f"{CLR_YELLOW}Dialing {args.dial}...{CLR_RESET}")
        res = send_backend_phone_request("dial", {"number": args.dial})
        print("Result:", json.dumps(res, indent=2))
        return

    if args.hangup:
        print(f"{CLR_RED}Triggering Hangup...{CLR_RESET}")
        res = send_backend_phone_request("action", {"action": "hangup"})
        print("Result:", json.dumps(res, indent=2))
        return

    if args.answer:
        print(f"{CLR_GREEN}Triggering Answer...{CLR_RESET}")
        res = send_backend_phone_request("action", {"action": "answer"})
        print("Result:", json.dumps(res, indent=2))
        return

    if args.dtmf:
        print(f"{CLR_CYAN}Sending DTMF key '{args.dtmf}'...{CLR_RESET}")
        res = send_backend_phone_request("dtmf", {"key": args.dtmf})
        print("Result:", json.dumps(res, indent=2))
        return

    if args.sync:
        print(f"{CLR_BLUE}Triggering PBAP Phonebook Sync...{CLR_RESET}")
        res = send_backend_phone_request("sync", {})
        print("Result:", json.dumps(res, indent=2))
        return

    # Default to monitor if no action specified or if --monitor requested
    run_btmon_monitor(log_file=args.log, raw_mode=args.raw)


if __name__ == "__main__":
    main()
