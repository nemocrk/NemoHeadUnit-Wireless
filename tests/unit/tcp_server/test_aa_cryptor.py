import ssl
from unittest.mock import MagicMock
import pytest
from modules.tcp_server.aa_cryptor import AACryptor

pytestmark = pytest.mark.unit


def test_aa_cryptor_init_and_deinit():
    cryptor = AACryptor()
    assert not cryptor.is_active()
    assert cryptor.get_in_bio_pending() == 0
    assert cryptor.get_out_bio_pending() == 0

    cryptor.init()
    assert cryptor._ctx is not None
    assert cryptor._ssl_obj is not None
    assert cryptor._in_bio is not None
    assert cryptor._out_bio is not None
    assert not cryptor.is_active()

    cryptor.deinit()
    assert cryptor._ctx is None
    assert cryptor._ssl_obj is None
    assert cryptor._in_bio is None
    assert cryptor._out_bio is None
    assert not cryptor.is_active()
    assert cryptor.get_in_bio_pending() == 0
    assert cryptor.get_out_bio_pending() == 0


def test_aa_cryptor_handshake_client_hello():
    cryptor = AACryptor()
    cryptor.init()

    # Client speaks first — drive_handshake() generates ClientHello
    client_hello = cryptor.drive_handshake()
    assert len(client_hello) > 0
    # TLS Handshake record header: 0x16 (Handshake), version 0x03 0x01 or 0x03 0x03
    assert client_hello[0] == 0x16
    assert client_hello[1] == 0x03

    parsed = cryptor.parse_tls_record_header(client_hello)
    assert parsed["valid"] is True
    assert parsed["content_type"] == 0x16
    assert parsed["type_name"] == "Handshake"
    cryptor.deinit()


def test_aa_cryptor_parse_tls_record_header_varieties():
    cryptor = AACryptor()

    # Short payload (< 5 bytes)
    assert cryptor.parse_tls_record_header(b"\x16\x03")["valid"] is False

    # ApplicationData (0x17)
    app_data = b"\x17\x03\x03\x00\x04test"
    parsed_app = cryptor.parse_tls_record_header(app_data)
    assert parsed_app["valid"] is True
    assert parsed_app["type_name"] == "ApplicationData"
    assert parsed_app["record_len"] == 4
    assert parsed_app["len_match"] is True

    # Alert (0x15)
    alert_data = b"\x15\x03\x03\x00\x02\x01\x00"
    parsed_alert = cryptor.parse_tls_record_header(alert_data)
    assert parsed_alert["type_name"] == "Alert"

    # ChangeCipherSpec (0x14)
    ccs_data = b"\x14\x03\x03\x00\x01\x01"
    parsed_ccs = cryptor.parse_tls_record_header(ccs_data)
    assert parsed_ccs["type_name"] == "ChangeCipherSpec"

    # Unknown content type
    unknown_data = b"\x99\x03\x03\x00\x01\x00"
    parsed_unknown = cryptor.parse_tls_record_header(unknown_data)
    assert parsed_unknown["type_name"] == "Unknown(0x99)"


def test_aa_cryptor_encrypt_before_active_raises():
    cryptor = AACryptor()
    cryptor.init()
    with pytest.raises(RuntimeError, match="before handshake complete"):
        cryptor.encrypt(b"secret")
    with pytest.raises(RuntimeError, match="before handshake complete"):
        cryptor.decrypt(b"secret")
    cryptor.deinit()


def test_aa_cryptor_encrypt_records_splitting():
    cryptor = AACryptor()
    cryptor._active = True
    cryptor._ssl_obj = object()  # dummy object to pass guard

    # Synthetic multi-record stream: record1 (len 4) + record2 (len 2)
    rec1 = b"\x17\x03\x03\x00\x04ABCD"
    rec2 = b"\x17\x03\x03\x00\x02EF"
    stream = rec1 + rec2

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cryptor, "encrypt", lambda data: stream)
        records = cryptor.encrypt_records(b"input")
        assert len(records) == 2
        assert records[0] == rec1
        assert records[1] == rec2


def test_aa_cryptor_encrypt_records_edge_cases():
    cryptor = AACryptor()
    cryptor._active = True
    cryptor._ssl_obj = object()

    with pytest.MonkeyPatch.context() as mp:
        # Empty ciphertext
        mp.setattr(cryptor, "encrypt", lambda data: b"")
        assert cryptor.encrypt_records(b"") == []

        # Incomplete record header (< 5 bytes tail)
        short_tail = b"\x17\x03\x03"
        mp.setattr(cryptor, "encrypt", lambda data: short_tail)
        assert cryptor.encrypt_records(b"short") == [short_tail]

        # Truncated record payload (offset + full_rec_len > total_len)
        truncated = b"\x17\x03\x03\x00\x0a1234"  # claims 10 bytes payload, only has 4
        mp.setattr(cryptor, "encrypt", lambda data: truncated)
        assert cryptor.encrypt_records(b"trunc") == [truncated]


def test_aa_cryptor_write_handshake_input_and_pending():
    cryptor = AACryptor()
    # When uninitialized, write_handshake_input is a safe no-op
    cryptor.write_handshake_input(b"bytes")
    assert cryptor.get_in_bio_pending() == 0

    cryptor.init()
    cryptor.write_handshake_input(b"incoming_handshake_data")
    assert cryptor.get_in_bio_pending() == len(b"incoming_handshake_data")
    cryptor.deinit()


def test_aa_cryptor_drive_handshake_uninitialized_and_errors():
    cryptor = AACryptor()
    # Uninitialized returns empty bytes
    assert cryptor.drive_handshake() == b""

    # Error simulation during handshake
    cryptor.init()
    mock_ssl = MagicMock()
    mock_ssl.do_handshake.side_effect = ssl.SSLError("handshake failure")
    cryptor._ssl_obj = mock_ssl
    out = cryptor.drive_handshake()
    assert out == b""
    assert not cryptor.is_active()
    cryptor.deinit()


def test_aa_cryptor_post_handshake_mock_encrypt_decrypt():
    cryptor = AACryptor()
    cryptor.init()
    cryptor._active = True

    mock_ssl = MagicMock()
    cryptor._ssl_obj = mock_ssl

    # Test encrypt writes to ssl_obj and drains out_bio
    cryptor._out_bio.write(b"ciphertext_stream")
    ciphertext = cryptor.encrypt(b"plaintext")
    mock_ssl.write.assert_called_once_with(b"plaintext")
    assert ciphertext == b"ciphertext_stream"

    # Test decrypt writes to in_bio and reads from ssl_obj
    mock_ssl.read.side_effect = [b"decrypted_chunk", ssl.SSLWantReadError]
    plaintext = cryptor.decrypt(b"ciphertext_stream")
    assert plaintext == b"decrypted_chunk"
    cryptor.deinit()
