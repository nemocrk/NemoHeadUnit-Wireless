"""
test_bluez_pbap.py — Unit tests for BlueZ PBAP client and parsers (shared.hardware.bluez_pbap)
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from shared.hardware.bluez_pbap import (
    BlueZPBAPClient,
    parse_vcard_stream,
    parse_call_history_stream,
    _get_default_cache_path,
)

pytestmark = pytest.mark.unit


def test_parse_vcard_stream_contacts():
    vcard_data = """
BEGIN:VCARD
VERSION:2.1
FN:John Doe
N:Doe;John;;;
TEL;CELL:+15551234
TEL;WORK:+15555678
CATEGORIES:FAVORITES
END:VCARD
BEGIN:VCARD
VERSION:2.1
FN:Jane Smith
TEL:+15559999
X-FAVORITE:1
END:VCARD
BEGIN:VCARD
VERSION:2.1
TEL:+15550000
END:VCARD
"""
    contacts = parse_vcard_stream(vcard_data)
    assert len(contacts) == 3

    assert contacts[0]["name"] == "John Doe"
    assert contacts[0]["primary_phone"] == "+15551234"
    assert contacts[0]["favorite"] is True
    assert len(contacts[0]["phones"]) == 2
    assert contacts[0]["phones"][0]["type"] == "CELL"
    assert contacts[0]["phones"][1]["type"] == "WORK"

    assert contacts[1]["name"] == "Jane Smith"
    assert contacts[1]["primary_phone"] == "+15559999"
    assert contacts[1]["favorite"] is True

    # Fallback to phone number when name is absent
    assert contacts[2]["name"] == "+15550000"
    assert contacts[2]["favorite"] is False


def test_parse_call_history_stream():
    call_data = """
BEGIN:VCARD
VERSION:2.1
FN:Mom
TEL:+15551111
X-IRMC-CALL-DATETIME;RECEIVED:20260908T070000
END:VCARD
BEGIN:VCARD
VERSION:2.1
FN:Work
TEL:+15552222
X-IRMC-CALL-DATETIME;DIALED:20260908T073000
END:VCARD
BEGIN:VCARD
VERSION:2.1
TEL:+15553333
X-IRMC-CALL-DATETIME;MISSED:20260908T080000
END:VCARD
"""
    calls = parse_call_history_stream(call_data)
    assert len(calls) == 3

    assert calls[0]["name"] == "Mom"
    assert calls[0]["call_type"] == "RECEIVED"
    assert calls[0]["timestamp"] == "20260908T070000"

    assert calls[1]["name"] == "Work"
    assert calls[1]["call_type"] == "DIALED"

    assert calls[2]["name"] == "+15553333"
    assert calls[2]["call_type"] == "MISSED"


def test_pbap_client_cache_and_getters(tmp_path):
    cache_file = tmp_path / "test_pbap_cache.json"

    # Initial empty / non-existent cache
    client = BlueZPBAPClient(cache_path=str(cache_file))
    assert client.get_contacts() == []
    assert client.get_recents() == []
    assert client.get_favorites() == []

    mock_contacts = [
        {"name": "Alice", "primary_phone": "111", "favorite": True},
        {"name": "Bob", "primary_phone": "222", "favorite": False},
    ]
    mock_recents = [
        {"name": "Alice", "number": "111", "call_type": "RECEIVED"},
    ]

    client.save_cache(contacts=mock_contacts, recents=mock_recents)
    assert cache_file.exists()

    # Re-instantiate from saved file
    client2 = BlueZPBAPClient(cache_path=str(cache_file))
    assert len(client2.get_contacts()) == 2
    assert len(client2.get_recents()) == 1
    favorites = client2.get_favorites()
    assert len(favorites) == 1
    assert favorites[0]["name"] == "Alice"


@pytest.mark.asyncio
async def test_pbap_client_sync_empty_address(tmp_path):
    cache_file = tmp_path / "cache.json"
    client = BlueZPBAPClient(cache_path=str(cache_file))

    res = await client.sync("")
    assert res["status"] == "ok"
    assert res["cached"] is True


@pytest.mark.asyncio
async def test_pbap_client_sync_concurrent_lock(tmp_path):
    cache_file = tmp_path / "cache.json"
    client = BlueZPBAPClient(cache_path=str(cache_file))

    # Acquire lock simulating sync in progress
    client._sync_lock.acquire()
    try:
        res = await client.sync("AA:BB:CC:DD:EE:FF")
        assert res["status"] == "ok"
        assert res["cached"] is True
    finally:
        client._sync_lock.release()


def test_get_default_cache_path(monkeypatch, tmp_path):
    monkeypatch.setenv("NEMO_CONFIG_DIR", str(tmp_path))
    p = _get_default_cache_path()
    assert p == tmp_path / "pbap_cache.json"


def test_pbap_do_sync_dbus_obex_no_bus(tmp_path):
    cache_file = tmp_path / "cache.json"
    client = BlueZPBAPClient(cache_path=str(cache_file))

    with patch("dbus.SystemBus", side_effect=Exception("no bus")), \
         patch("dbus.SessionBus", side_effect=Exception("no bus")):
        res = client._do_sync_dbus_obex("AA:BB:CC:DD:EE:FF")
        assert res["status"] == "ok"
        assert res["cached"] is True


def test_pbap_do_sync_dbus_obex_success(tmp_path):
    cache_file = tmp_path / "cache.json"
    client = BlueZPBAPClient(cache_path=str(cache_file))

    mock_bus = MagicMock()
    mock_obex_obj = MagicMock()
    mock_bus.get_object.return_value = mock_obex_obj

    mock_client = MagicMock()
    mock_client.CreateSession.return_value = "/org/bluez/obex/session0"

    mock_pbap = MagicMock()

    def fake_pull(file_path, filters):
        # Write dummy vcard into file_path
        Path(file_path).write_text("BEGIN:VCARD\nVERSION:2.1\nFN:Alice\nTEL:123\nEND:VCARD\n")
        return ("/org/bluez/obex/transfer0", None)

    mock_pbap.PullAll.side_effect = fake_pull

    mock_props = MagicMock()
    mock_props.Get.return_value = "complete"

    def iface_side_effect(obj, iface_name):
        if iface_name == "org.bluez.obex.Client1":
            return mock_client
        if iface_name == "org.bluez.obex.PhonebookAccess1":
            return mock_pbap
        if iface_name == "org.freedesktop.DBus.Properties":
            return mock_props
        return MagicMock()

    with patch("dbus.SystemBus", return_value=mock_bus), \
         patch("dbus.Interface", side_effect=iface_side_effect):
        res = client._do_sync_dbus_obex("AA:BB:CC:DD:EE:FF")
        assert res["status"] == "ok"
        assert res["cached"] is False
        assert len(client.get_contacts()) > 0
        mock_client.RemoveSession.assert_called_once_with("/org/bluez/obex/session0")

