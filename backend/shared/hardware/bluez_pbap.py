"""
bluez_pbap.py — Standalone Bluetooth Phone Book Access Profile (PBAP) client.
Syncs and caches contacts, favorites, and call history via BlueZ OBEX vCard parser.
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from shared.logger import get_logger

log = get_logger("hardware.bluez_pbap")

DEFAULT_SYNTHETIC_CONTACTS: List[Dict[str, Any]] = []
DEFAULT_SYNTHETIC_RECENTS: List[Dict[str, Any]] = []


def _get_default_cache_path() -> Path:
    config_dir = os.environ.get("NEMO_CONFIG_DIR")
    if config_dir:
        base = Path(config_dir)
    elif os.name == "nt":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        base = Path(appdata) / "NemoHeadUnit-Wireless"
    else:
        base = Path.home() / ".config" / "NemoHeadUnit-Wireless"
    base.mkdir(parents=True, exist_ok=True)
    return base / "pbap_cache.json"


def parse_vcard_stream(text: str) -> List[Dict[str, Any]]:
    """Parse a stream of vCard 2.1/3.0 contact cards."""
    contacts: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line == "BEGIN:VCARD":
            current = {
                "name": "",
                "phones": [],
                "favorite": False,
                "primary_phone": "",
            }
            continue

        if line == "END:VCARD" and current is not None:
            if not current["name"]:
                current["name"] = current["primary_phone"] or "Unknown"
            if not current["primary_phone"] and current["phones"]:
                current["primary_phone"] = current["phones"][0]["number"]
            contacts.append(current)
            current = None
            continue

        if current is None:
            continue

        if line.startswith("FN:"):
            current["name"] = line[3:].strip()
        elif line.startswith("N:") and not current["name"]:
            parts = line[2:].split(";")
            last = parts[0].strip() if len(parts) > 0 else ""
            first = parts[1].strip() if len(parts) > 1 else ""
            name = f"{first} {last}".strip()
            if name:
                current["name"] = name
        elif line.startswith("TEL"):
            # e.g. TEL;CELL:+123 or TEL;TYPE=CELL,VOICE:+123 or TEL:+123
            tel_parts = line.split(":", 1)
            if len(tel_parts) == 2:
                meta = tel_parts[0].upper()
                number = tel_parts[1].strip()
                phone_type = "GENERAL"
                if "CELL" in meta or "MOBILE" in meta:
                    phone_type = "CELL"
                elif "HOME" in meta:
                    phone_type = "HOME"
                elif "WORK" in meta:
                    phone_type = "WORK"

                current["phones"].append({"number": number, "type": phone_type})
                if not current["primary_phone"]:
                    current["primary_phone"] = number
        elif "CATEGORIES:" in line.upper() and "FAVORITE" in line.upper():
            current["favorite"] = True
        elif "X-FAVORITE:1" in line.upper():
            current["favorite"] = True

    return contacts


def parse_call_history_stream(text: str) -> List[Dict[str, Any]]:
    """Parse vCard 2.1/3.0 call history (cch.vcf)."""
    calls: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line == "BEGIN:VCARD":
            current = {
                "name": "",
                "number": "",
                "call_type": "RECEIVED",
                "timestamp": "",
            }
            continue

        if line == "END:VCARD" and current is not None:
            if not current["name"]:
                current["name"] = current["number"] or "Unknown"
            calls.append(current)
            current = None
            continue

        if current is None:
            continue

        if line.startswith("FN:"):
            current["name"] = line[3:].strip()
        elif line.startswith("TEL"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                current["number"] = parts[1].strip()
        elif "X-IRMC-CALL-DATETIME" in line.upper():
            parts = line.split(":", 1)
            if len(parts) == 2:
                current["timestamp"] = parts[1].strip()
            meta = parts[0].upper()
            if "DIALED" in meta or "MISSED" in meta or "RECEIVED" in meta:
                for ctype in ("DIALED", "MISSED", "RECEIVED"):
                    if ctype in meta:
                        current["call_type"] = ctype
                        break

    return calls


class BlueZPBAPClient:
    """
    PBAP Client providing persistent local caching, OBEX synchronization,
    and synthetic fallbacks when offline or un-synced.
    """

    def __init__(self, cache_path: Optional[str] = None):
        self._cache_file = Path(cache_path) if cache_path else _get_default_cache_path()
        self._contacts: List[Dict[str, Any]] = []
        self._recents: List[Dict[str, Any]] = []
        self._load_cache()

    def _load_cache(self) -> None:
        if self._cache_file.exists():
            try:
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._contacts = data.get("contacts", [])
                    self._recents = data.get("recents", [])
                log.info(f"Loaded PBAP cache from {self._cache_file} ({len(self._contacts)} contacts, {len(self._recents)} recents)")
                return
            except Exception as e:
                log.warning(f"Failed to read PBAP cache file {self._cache_file}: {e}")

        # Default synthetic data
        self._contacts = list(DEFAULT_SYNTHETIC_CONTACTS)
        self._recents = list(DEFAULT_SYNTHETIC_RECENTS)

    def save_cache(self, contacts: Optional[List[Dict[str, Any]]] = None, recents: Optional[List[Dict[str, Any]]] = None) -> None:
        if contacts is not None:
            self._contacts = contacts
        if recents is not None:
            self._recents = recents

        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump({"contacts": self._contacts, "recents": self._recents}, f, indent=2)
            log.info(f"Saved PBAP cache to {self._cache_file}")
        except Exception as e:
            log.warning(f"Failed to write PBAP cache to {self._cache_file}: {e}")

    def get_contacts(self) -> List[Dict[str, Any]]:
        return list(self._contacts)

    def get_favorites(self) -> List[Dict[str, Any]]:
        return [c for c in self._contacts if c.get("favorite")]

    def get_recents(self) -> List[Dict[str, Any]]:
        return list(self._recents)

    async def sync(self, device_address: str = "") -> Dict[str, Any]:
        """
        Attempt PBAP phonebook pull via BlueZ OBEX over D-Bus.
        Cross-platform compliant: on Windows or when OBEX is unreachable, returns cached data safely.
        """
        log.info(f"Triggered PBAP Phonebook Sync for device '{device_address}'")
        if sys.platform == "win32" or not device_address:
            return {
                "status": "ok",
                "contacts_count": len(self._contacts),
                "recents_count": len(self._recents),
                "cached": True,
            }

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, self._sync_dbus_obex, device_address)
            return result
        except Exception as e:
            log.warning(f"PBAP D-Bus sync failed: {e}, using cached data")
            return {
                "status": "ok",
                "contacts_count": len(self._contacts),
                "recents_count": len(self._recents),
                "cached": True,
                "error": str(e),
            }

    def _sync_dbus_obex(self, device_address: str) -> Dict[str, Any]:
        import dbus
        import tempfile
        import time

        bus = None
        for get_bus in (dbus.SystemBus, dbus.SessionBus):
            try:
                b = get_bus()
                b.get_object("org.bluez.obex", "/org/bluez/obex")
                bus = b
                break
            except Exception:
                continue

        if bus is None:
            log.warning("No D-Bus connection to org.bluez.obex found")
            return {
                "status": "ok",
                "contacts_count": len(self._contacts),
                "recents_count": len(self._recents),
                "cached": True,
            }

        obex_obj = bus.get_object("org.bluez.obex", "/org/bluez/obex")
        client = dbus.Interface(obex_obj, "org.bluez.obex.Client1")

        session_path = None
        new_contacts = None
        new_recents = None

        try:
            log.info(f"Creating OBEX PBAP session for {device_address}...")
            session_opts = dbus.Dictionary({"Target": "PBAP"}, signature="sv")
            session_path = client.CreateSession(device_address, session_opts)
            log.info(f"OBEX PBAP session established at {session_path}")

            session_obj = bus.get_object("org.bluez.obex", session_path)
            pbap = dbus.Interface(session_obj, "org.bluez.obex.PhonebookAccess1")

            obex_dir = Path("/var/lib/obex")
            if obex_dir.exists() and os.access(str(obex_dir), os.W_OK):
                work_dir = obex_dir
            else:
                work_dir = Path(tempfile.gettempdir())

            clean_addr = device_address.replace(":", "_")
            ts = int(time.time())
            pid = os.getpid()
            contacts_file = work_dir / f"pb_{clean_addr}_{pid}_{ts}_contacts.vcf"
            recents_file = work_dir / f"pb_{clean_addr}_{pid}_{ts}_recents.vcf"
            empty_filters = dbus.Dictionary({}, signature="sv")

            def _wait_for_transfer(transfer_path: str, target_file: Path, max_wait: float = 12.0):
                transfer_obj = bus.get_object("org.bluez.obex", transfer_path)
                props_iface = dbus.Interface(transfer_obj, "org.freedesktop.DBus.Properties")
                deadline = time.time() + max_wait
                while time.time() < deadline:
                    try:
                        status = str(props_iface.Get("org.bluez.obex.Transfer1", "Status"))
                        if status in ("complete", "error"):
                            break
                    except Exception:
                        pass
                    if target_file.exists() and target_file.stat().st_size > 0:
                        break
                    time.sleep(0.2)

            # 1. Pull Contacts (pb)
            try:
                pbap.Select("int", "pb")
                transfer_path, _ = pbap.PullAll(str(contacts_file), empty_filters)
                _wait_for_transfer(transfer_path, contacts_file)
                if contacts_file.exists() and contacts_file.stat().st_size > 0:
                    text = contacts_file.read_text(encoding="utf-8", errors="replace")
                    parsed = parse_vcard_stream(text)
                    if parsed:
                        new_contacts = parsed
                        log.info(f"Parsed {len(parsed)} contacts from OBEX vCard stream")
            except Exception as ce:
                log.warning(f"Failed to pull contacts: {ce}")
            finally:
                try:
                    contacts_file.unlink(missing_ok=True)
                except Exception:
                    pass

            # 2. Pull Call History (cch)
            try:
                pbap.Select("int", "cch")
                transfer_path, _ = pbap.PullAll(str(recents_file), empty_filters)
                _wait_for_transfer(transfer_path, recents_file)
                if recents_file.exists() and recents_file.stat().st_size > 0:
                    text = recents_file.read_text(encoding="utf-8", errors="replace")
                    parsed_rec = parse_call_history_stream(text)
                    if parsed_rec:
                        new_recents = parsed_rec
                        log.info(f"Parsed {len(parsed_rec)} call history entries from OBEX vCard stream")
            except Exception as re_err:
                log.warning(f"Failed to pull call history: {re_err}")
            finally:
                try:
                    recents_file.unlink(missing_ok=True)
                except Exception:
                    pass

            if new_contacts is not None or new_recents is not None:
                self.save_cache(contacts=new_contacts, recents=new_recents)

            return {
                "status": "ok",
                "contacts_count": len(self._contacts),
                "recents_count": len(self._recents),
                "cached": False,
            }
        except Exception as se:
            log.warning(f"Error during OBEX PBAP session: {se}")
            return {
                "status": "ok",
                "contacts_count": len(self._contacts),
                "recents_count": len(self._recents),
                "cached": True,
                "error": str(se),
            }
        finally:
            if session_path and client:
                try:
                    client.RemoveSession(session_path)
                except Exception as clean_err:
                    log.debug(f"Error removing OBEX session: {clean_err}")
