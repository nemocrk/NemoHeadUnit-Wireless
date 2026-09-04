"""
test_pbap_vcard_parser.py — Tests for PBAP vCard parsing and caching.
"""

import os
import json
import pytest
from backend.shared.hardware.bluez_pbap import (
    parse_vcard_stream,
    parse_call_history_stream,
    BlueZPBAPClient,
)

SAMPLE_VCARDS = """BEGIN:VCARD
VERSION:2.1
N:Rossi;Mario;;;
FN:Mario Rossi
TEL;CELL:+39333112233
TEL;HOME:02890011
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Giulia Bianchi
TEL;TYPE=CELL:+39347556677
CATEGORIES:FAVORITE
END:VCARD
"""

SAMPLE_CALL_HISTORY = """BEGIN:VCARD
VERSION:2.1
FN:Mario Rossi
TEL:+39333112233
X-IRMC-CALL-DATETIME;DIALED:20260904T183000
END:VCARD
BEGIN:VCARD
VERSION:2.1
FN:Emergency Contact
TEL:112
X-IRMC-CALL-DATETIME;MISSED:20260904T174512
END:VCARD
BEGIN:VCARD
VERSION:2.1
FN:Giulia Bianchi
TEL:+39347556677
X-IRMC-CALL-DATETIME;RECEIVED:20260904T161000
END:VCARD
"""


def test_parse_vcard_stream():
    contacts = parse_vcard_stream(SAMPLE_VCARDS)
    assert len(contacts) == 2

    c1 = contacts[0]
    assert c1["name"] == "Mario Rossi"
    assert c1["primary_phone"] == "+39333112233"
    assert len(c1["phones"]) == 2
    assert c1["phones"][0]["number"] == "+39333112233"
    assert c1["phones"][1]["type"] == "HOME"
    assert c1["favorite"] is False

    c2 = contacts[1]
    assert c2["name"] == "Giulia Bianchi"
    assert c2["primary_phone"] == "+39347556677"
    assert c2["favorite"] is True


def test_parse_call_history_stream():
    calls = parse_call_history_stream(SAMPLE_CALL_HISTORY)
    assert len(calls) == 3

    assert calls[0]["name"] == "Mario Rossi"
    assert calls[0]["number"] == "+39333112233"
    assert calls[0]["call_type"] == "DIALED"
    assert "20260904T183000" in calls[0]["timestamp"]

    assert calls[1]["name"] == "Emergency Contact"
    assert calls[1]["number"] == "112"
    assert calls[1]["call_type"] == "MISSED"

    assert calls[2]["name"] == "Giulia Bianchi"
    assert calls[2]["call_type"] == "RECEIVED"


def test_bluez_pbap_client_cache_and_synthetic_fallback(tmp_path):
    cache_file = tmp_path / "test_pbap_cache.json"
    client = BlueZPBAPClient(cache_path=str(cache_file))

    # Initial empty cache returns synthetic fallback contacts & recents
    contacts = client.get_contacts()
    assert len(contacts) > 0
    assert any(c["name"] == "Emergency Assistance" or "Assistance" in c["name"] for c in contacts)

    recents = client.get_recents()
    assert len(recents) > 0

    # Save contacts to cache
    custom_contacts = [
        {"name": "Dev Team", "primary_phone": "1001", "favorite": True, "phones": [{"number": "1001", "type": "WORK"}]}
    ]
    client.save_cache(contacts=custom_contacts)

    # Reopen client and verify cache was loaded
    client2 = BlueZPBAPClient(cache_path=str(cache_file))
    loaded = client2.get_contacts()
    assert len(loaded) == 1
    assert loaded[0]["name"] == "Dev Team"
    assert client2.get_favorites()[0]["name"] == "Dev Team"
