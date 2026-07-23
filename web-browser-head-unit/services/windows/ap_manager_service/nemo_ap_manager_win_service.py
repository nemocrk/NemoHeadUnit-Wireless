#!/usr/bin/env python3
import asyncio
import json
import logging
import sys

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NemoAPManagerService] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("NemoAPManagerService")

NEMO_AP_MANAGER_PORT = 15288

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        data = await reader.readline()
        if not data:
            return
        
        cmd = json.loads(data.decode().strip())
        action = cmd.get("action")
        log.info(f"Received action: {action}")

        if action == "start":
            ssid = cmd.get("ssid", "AndroidAutoAP")
            key = cmd.get("key", "12345678")
            success, err_msg = await start_windows_hotspot(ssid, key)
            if success:
                resp = {"status": "ok"}
            else:
                resp = {"status": "error", "message": err_msg}
        elif action == "stop":
            success, err_msg = await stop_windows_hotspot()
            if success:
                resp = {"status": "ok"}
            else:
                resp = {"status": "error", "message": err_msg}
        else:
            resp = {"status": "error", "message": f"Unknown action: {action}"}

        writer.write(json.dumps(resp).encode() + b"\n")
        await writer.drain()
    except Exception as e:
        log.error(f"Error handling service client: {e}")
        try:
            resp = {"status": "error", "message": str(e)}
            writer.write(json.dumps(resp).encode() + b"\n")
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()
        await writer.wait_closed()

async def start_windows_hotspot(ssid: str, key: str) -> tuple[bool, str]:
    try:
        import winrt.windows.networking.connectivity as connectivity
        import winrt.windows.networking.networkoperators as netops

        profile = connectivity.NetworkInformation.get_internet_connection_profile()
        if not profile:
            return False, "No active internet connection profile found for tethering"
        
        tethering_mgr = netops.NetworkOperatorTetheringManager.create_from_connection_profile(profile)
        conf = netops.NetworkOperatorTetheringAccessPointConfiguration()
        conf.ssid = ssid
        conf.passphrase = key
        await tethering_mgr.configure_access_point_async(conf)
        
        result = await tethering_mgr.start_tethering_async()
        if result.status == netops.TetheringOperationStatus.SUCCESS:
            log.info(f"Tethering started successfully with SSID: {ssid}")
            return True, ""
        else:
            return False, f"Tethering operation failed with status: {result.status}"
    except Exception as e:
        log.error(f"Failed to configure/start Windows Hotspot: {e}")
        return False, str(e)

async def stop_windows_hotspot() -> tuple[bool, str]:
    try:
        import winrt.windows.networking.connectivity as connectivity
        import winrt.windows.networking.networkoperators as netops

        profile = connectivity.NetworkInformation.get_internet_connection_profile()
        if not profile:
            return False, "No active internet connection profile found for tethering"

        tethering_mgr = netops.NetworkOperatorTetheringManager.create_from_connection_profile(profile)
        result = await tethering_mgr.stop_tethering_async()
        if result.status == netops.TetheringOperationStatus.SUCCESS:
            log.info("Tethering stopped successfully")
            return True, ""
        else:
            return False, f"Tethering stop failed with status: {result.status}"
    except Exception as e:
        log.error(f"Failed to stop Windows Hotspot: {e}")
        return False, str(e)

async def main() -> None:
    log.info(f"Starting NemoAPManager background TCP service on port {NEMO_AP_MANAGER_PORT}...")
    server = await asyncio.start_server(handle_client, "127.0.0.1", NEMO_AP_MANAGER_PORT)
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("NemoAPManager service terminated")
