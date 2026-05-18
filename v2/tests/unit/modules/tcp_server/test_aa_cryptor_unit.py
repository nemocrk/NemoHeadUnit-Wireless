"""
Unit tests for tcp_server/aa_cryptor.py

Strategy:
  AACryptor uses ssl.MemoryBIO + ssl.SSLObject (no real network).
  Two test layers:
    1. Pure-mock layer: patch ssl.SSLContext / MemoryBIO to avoid real TLS —
       tests lifecycle, state machine branches, encrypt/decrypt error paths.
    2. Real-TLS layer (no patching): init() + drive_handshake() with an actual
       ssl.MemoryBIO pair to verify ClientHello is produced.

Covers:
  Section 1 — lifecycle: init/deinit, double-deinit, state after deinit
  Section 2 — write_handshake_input: before init, after init
  Section 3 — drive_handshake: before init, SSLWantReadError (normal), SSLError, active=True path
  Section 4 — encrypt / decrypt: before handshake (RuntimeError), mock post-handshake
  Section 5 — _read_out_bio: None bio guard, empty read
  Section 6 — real TLS smoke: init() produces ClientHello bytes on first drive_handshake()
"""

import ssl
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

import sys
from pathlib import Path

_V2 = Path(__file__).parents[4]
if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))

with patch("shared.logger.get_logger", return_value=MagicMock()):
    from tcp_server.aa_cryptor import AACryptor


# ===========================================================================
# Section 1 — Lifecycle
# ===========================================================================

class TestLifecycle:

    @pytest.mark.unit
    def test_initial_state_not_active(self):
        c = AACryptor()
        assert not c.is_active()

    @pytest.mark.unit
    def test_init_sets_ssl_obj(self):
        c = AACryptor()
        c.init()
        assert c._ssl_obj is not None
        c.deinit()

    @pytest.mark.unit
    def test_deinit_clears_all_fields(self):
        c = AACryptor()
        c.init()
        c.deinit()
        assert c._ssl_obj is None
        assert c._in_bio is None
        assert c._out_bio is None
        assert c._ctx is None
        assert not c._active

    @pytest.mark.unit
    def test_double_deinit_no_exception(self):
        c = AACryptor()
        c.init()
        c.deinit()
        c.deinit()  # must not raise

    @pytest.mark.unit
    def test_is_active_false_after_deinit(self):
        c = AACryptor()
        c.init()
        c.deinit()
        assert not c.is_active()


# ===========================================================================
# Section 2 — write_handshake_input
# ===========================================================================

class TestWriteHandshakeInput:

    @pytest.mark.unit
    def test_write_before_init_no_crash(self):
        c = AACryptor()  # _in_bio is None
        c.write_handshake_input(b"data")  # must not raise

    @pytest.mark.unit
    def test_write_after_init_feeds_in_bio(self):
        c = AACryptor()
        c.init()
        data = b"\x01\x02\x03"
        c.write_handshake_input(data)
        # in_bio should now have pending bytes
        assert c._in_bio.pending >= len(data)
        c.deinit()


# ===========================================================================
# Section 3 — drive_handshake (mocked ssl)
# ===========================================================================

class TestDriveHandshake:

    @pytest.mark.unit
    def test_drive_before_init_returns_empty(self):
        c = AACryptor()
        assert c.drive_handshake() == b""

    @pytest.mark.unit
    def test_drive_ssl_want_read_returns_pending_bytes(self):
        """SSLWantReadError is normal — method must return whatever out_bio has."""
        c = AACryptor()
        mock_ssl = MagicMock()
        mock_ssl.do_handshake.side_effect = ssl.SSLWantReadError
        mock_out_bio = MagicMock()
        mock_out_bio.read.return_value = b"ClientHello"
        c._ssl_obj  = mock_ssl
        c._out_bio  = mock_out_bio
        c._in_bio   = MagicMock()
        result = c.drive_handshake()
        assert result == b"ClientHello"
        assert not c._active

    @pytest.mark.unit
    def test_drive_ssl_error_returns_empty_no_raise(self):
        c = AACryptor()
        mock_ssl = MagicMock()
        mock_ssl.do_handshake.side_effect = ssl.SSLError("fatal")
        mock_out_bio = MagicMock()
        mock_out_bio.read.return_value = b""
        c._ssl_obj = mock_ssl
        c._out_bio = mock_out_bio
        c._in_bio  = MagicMock()
        result = c.drive_handshake()  # must not raise
        assert result == b""

    @pytest.mark.unit
    def test_drive_handshake_success_sets_active(self):
        c = AACryptor()
        mock_ssl = MagicMock()
        mock_ssl.do_handshake.return_value = None  # no exception = success
        mock_out_bio = MagicMock()
        mock_out_bio.read.return_value = b"Finished"
        c._ssl_obj = mock_ssl
        c._out_bio = mock_out_bio
        c._in_bio  = MagicMock()
        c.drive_handshake()
        assert c._active is True

    @pytest.mark.unit
    def test_drive_skips_do_handshake_when_already_active(self):
        c = AACryptor()
        mock_ssl = MagicMock()
        c._ssl_obj = mock_ssl
        c._out_bio = MagicMock()
        c._out_bio.read.return_value = b""
        c._in_bio  = MagicMock()
        c._active  = True
        c.drive_handshake()
        mock_ssl.do_handshake.assert_not_called()


# ===========================================================================
# Section 4 — encrypt / decrypt
# ===========================================================================

class TestEncryptDecrypt:

    @pytest.mark.unit
    def test_encrypt_before_handshake_raises(self):
        c = AACryptor()
        with pytest.raises(RuntimeError):
            c.encrypt(b"plain")

    @pytest.mark.unit
    def test_decrypt_before_handshake_raises(self):
        c = AACryptor()
        with pytest.raises(RuntimeError):
            c.decrypt(b"cipher")

    @pytest.mark.unit
    def test_encrypt_post_handshake_calls_ssl_write(self):
        c = AACryptor()
        mock_ssl = MagicMock()
        mock_out_bio = MagicMock()
        mock_out_bio.read.return_value = b"CIPHER"
        c._ssl_obj = mock_ssl
        c._out_bio = mock_out_bio
        c._in_bio  = MagicMock()
        c._active  = True
        result = c.encrypt(b"plaintext")
        mock_ssl.write.assert_called_once_with(b"plaintext")
        assert result == b"CIPHER"

    @pytest.mark.unit
    def test_decrypt_post_handshake_reads_ssl(self):
        c = AACryptor()
        mock_ssl = MagicMock()
        mock_ssl.read.side_effect = [b"decrypted_chunk", ssl.SSLWantReadError]
        mock_in_bio = MagicMock()
        c._ssl_obj = mock_ssl
        c._in_bio  = mock_in_bio
        c._out_bio = MagicMock()
        c._active  = True
        result = c.decrypt(b"ciphertext")
        mock_in_bio.write.assert_called_once_with(b"ciphertext")
        assert result == b"decrypted_chunk"

    @pytest.mark.unit
    def test_decrypt_empty_ssl_read_stops_loop(self):
        c = AACryptor()
        mock_ssl = MagicMock()
        mock_ssl.read.return_value = b""  # EOF
        c._ssl_obj = mock_ssl
        c._in_bio  = MagicMock()
        c._out_bio = MagicMock()
        c._active  = True
        result = c.decrypt(b"x")
        assert result == b""


# ===========================================================================
# Section 5 — _read_out_bio
# ===========================================================================

class TestReadOutBio:

    @pytest.mark.unit
    def test_read_out_bio_none_bio_returns_empty(self):
        c = AACryptor()
        c._out_bio = None
        assert c._read_out_bio() == b""

    @pytest.mark.unit
    def test_read_out_bio_empty_read_returns_empty(self):
        c = AACryptor()
        mock_bio = MagicMock()
        mock_bio.read.return_value = b""
        c._out_bio = mock_bio
        assert c._read_out_bio() == b""

    @pytest.mark.unit
    def test_read_out_bio_none_read_returns_empty(self):
        c = AACryptor()
        mock_bio = MagicMock()
        mock_bio.read.return_value = None
        c._out_bio = mock_bio
        assert c._read_out_bio() == b""


# ===========================================================================
# Section 6 — Real TLS smoke test
# ===========================================================================

class TestRealTlsSmoke:

    @pytest.mark.unit
    def test_init_produces_client_hello_on_first_drive(self):
        """
        With a real ssl.MemoryBIO, drive_handshake() must return at least
        some bytes (ClientHello) even before the server responds.
        SSLWantReadError is expected and normal here.
        """
        c = AACryptor()
        c.init()
        out = c.drive_handshake()
        c.deinit()
        assert isinstance(out, bytes)
        assert len(out) > 0, "Expected ClientHello bytes from first drive_handshake()"

    @pytest.mark.unit
    def test_not_active_before_server_response(self):
        c = AACryptor()
        c.init()
        c.drive_handshake()
        assert not c.is_active()
        c.deinit()
