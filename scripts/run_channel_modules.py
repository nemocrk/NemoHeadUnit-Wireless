#!/usr/bin/env python3
"""
run_channel_modules.py — Standalone script to launch all channel modules.

This script simulates the channel_manager's session startup:
- Starts the bus_broker as a subprocess
- Generates a ServiceDiscoveryResponse with default channels
- Resolves and launches all implemented channel modules
- Waits indefinitely
- On KeyboardInterrupt: publishes channel_manager.shutdown, stops modules, kills broker
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

# Add paths
_REPO_ROOT = Path(__file__).parent.parent
_MODULES = _REPO_ROOT / "modules"

for p in (_REPO_ROOT, _MODULES):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from shared.bus_client import BusClient
from shared.logger import get_logger
from modules.channel_manager.registry import resolve_module_type, module_name, SkipChannel
from modules.channel_manager.launcher import Launcher
from modules.oaa_control_channel.service_discovery import build_from_schema_cfg, channels_from_sdr_bytes, SEMANTIC_DEFAULTS

MODULE_NAME = "run_channel_modules"
bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)

def main() -> None:
    # Start bus_broker
    log.info("Starting bus_broker...")
    broker_proc = subprocess.Popen([sys.executable, str(_REPO_ROOT / "bus_broker.py")])
    time.sleep(0.5)  # Let broker start

    # Connect to bus
    bus.start(blocking=False)

    # Generate SDR
    log.info("Generating ServiceDiscoveryResponse...")
    sdr_bytes = build_from_schema_cfg(SEMANTIC_DEFAULTS, bt_mac="", wifi_bssid="")
    sdr_hex = sdr_bytes.hex()

    # Get channels
    channels = channels_from_sdr_bytes(sdr_bytes)
    log.info("Found %d channels in SDR", len(channels))

    # Resolve and launch
    launcher = Launcher()
    launch_list = []

    for ch in channels:
        ch_id = ch.get("channel_id")
        if ch_id == 0:
            continue  # Control channel
        try:
            mtype = resolve_module_type(ch_id, ch)
        except SkipChannel as exc:
            log.warning("Skipping channel: %s", exc)
            continue
        except KeyError as exc:
            log.error("Cannot resolve channel: %s — skipping", exc)
            continue

        mname = module_name(mtype, ch_id)
        launch_list.append({
            "module_name": mname,
            "module_type": mtype,
            "channel_id": ch_id,
            "sdr_bytes_hex": sdr_hex,
        })

    if not launch_list:
        log.error("No channels to launch")
        return

    log.info("Launching %d channel modules: %s", len(launch_list), [d["module_name"] for d in launch_list])
    try:
        launcher.start_all(launch_list)
    except FileNotFoundError as exc:
        log.error("Failed to start modules: %s", exc)
        return

    log.info("All modules started. Waiting... (Ctrl+C to shutdown)")

    # Wait for interrupt
    try:
        while True:
            time.sleep(1)
            # Check for crashes
            crashed = launcher.check_crashes()
            if crashed:
                log.warning("Modules crashed: %s", crashed)
    except KeyboardInterrupt:
        log.info("Shutdown requested")

    # Shutdown
    log.info("Publishing channel_manager.shutdown...")
    bus.publish("channel_manager.shutdown", {})

    time.sleep(0.5)  # Let modules handle shutdown

    log.info("Stopping modules...")
    launcher.stop_all()

    log.info("Stopping bus...")
    broker_proc.terminate()
    try:
        broker_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        broker_proc.kill()
        broker_proc.wait()

    bus.stop()
    log.info("Shutdown complete")

if __name__ == "__main__":
    main()
