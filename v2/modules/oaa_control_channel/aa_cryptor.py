"""
aa_cryptor.py — In-band TLS for Android Auto (mirrors Cryptor.cpp from aasdk/opencardev).

Android Auto does NOT use TLS on the TCP socket directly.
Instead, TLS bytes are exchanged as AA frame payloads on channel 0, msgId 0x0003.

The HU is the TLS CLIENT (mirrors aasdk Cryptor.cpp: SSL_set_connect_state).
The phone is the TLS server.

Usage (client role):
    cryptor = AACryptor()
    cryptor.init()                          # SSL_set_connect_state

    # After VERSION_RESPONSE: HU speaks first in TLS
    out = cryptor.drive_handshake()         # returns ClientHello bytes
    # -> send out as frame ch=0 msgId=0x0003

    # Phone replies with ServerHello+Cert (frame 0x0003 payload)
    cryptor.write_handshake_input(server_hello_bytes)
    out = cryptor.drive_handshake()         # returns next TLS round bytes
    # -> send out as frame ch=0 msgId=0x0003

    # ... more rounds until cryptor.is_active() == True

    # After handshake complete:
    ciphertext = cryptor.encrypt(plaintext)
    plaintext  = cryptor.decrypt(ciphertext)
"""

from __future__ import annotations

import ssl
from typing import Optional

from shared.logger import get_logger

log = get_logger("oaa_control_channel.aa_cryptor")

# ---------------------------------------------------------------------------
# AA certificate + private key (from aasdk/opencardev Cryptor.cpp)
# The HU presents this cert as client certificate during the TLS handshake.
# ---------------------------------------------------------------------------

_AA_CERT_PEM = b"""-----BEGIN CERTIFICATE-----
MIIDKjCCAhICARswDQYJKoZIhvcNAQELBQAwWzELMAkGA1UEBhMCVVMxEzARBgNV
BAgMCkNhbGlmb3JuaWExFjAUBgNVBAcMDU1vdW50YWluIFZpZXcxHzAdBgNVBAoM
Fkdvb2dsZSBBdXRvbW90aXZlIExpbmswJhcRMTQwNzA0MDAwMDAwLTA3MDAXETQ1
MDQyOTE0MjgzOC0wNzAwMFMxCzAJBgNVBAYTAkpQMQ4wDAYDVQQIDAVUb2t5bzER
MA8GA1UEBwwISGFjaGlvamkxFDASBgNVBAoMC0pWQyBLZW53b29kMQswCQYDVQQL
DAIwMTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAM911mNnUfx+WJtx
uk06GO7kXRW/gXUVNQBkbAFZmVdVNvLoEQNthi2X8WCOwX6n6oMPxU2MGJnvicP3
6kBqfHhfQ2Fvqlf7YjjhgBHh0lqKShVPxIvdatBjVQ76aym5H3GpkigLGkmeyiVo
VO8oc3cJ1bO96wFRmk7kJbYcEjQyakODPDu4QgWUTwp1Z8Dn41ARMG5OFh6otITL
XBzj9REkUPkxfS03dBXGr5/LIqvSsnxib1hJ47xnYJXROUsBy3e6T+fYZEEzZa7y
7tFioHIQ8G/TziPmvFzmQpaWMGiYfoIgX8WoR3GD1diYW+wBaZTW+4SFUZJmRKgq
TbMNFkMCAwEAATANBgkqhkiG9w0BAQsFAAOCAQEAsGdH5VFn78WsBElMXaMziqFC
zmilkvr85/QpGCIztI0FdF6xyMBJk/gYs2thwvF+tCCpXoO8mjgJuvJZlwr6fHzK
Ox5hNUb06AeMtsUzUfFjSZXKrSR+XmclVd+Z6/ie33VhGePOPTKYmJ/PPfTT9wvT
93qswcxhA+oX5yqLbU3uDPF1ZnJaEeD/YN45K/4eEA4/0SDXaWW14OScdS2LV0Bc
YmsbkPVNYZn37FlY7e2Z4FUphh0A7yME2Eh/e57QxWrJ1wubdzGnX8mrABc67ADU
U5r9tlTRqMs7FGOk6QS2Cxp4pqeVQsrPts4OEwyPUyb3LfFNo3+sP111D9zEow==
-----END CERTIFICATE-----
"""

_AA_KEY_PEM = b"""-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAz3XWY2dR/H5Ym3G6TToY7uRdFb+BdRU1AGRsAVmZV1U28ugR
A22GLZfxYI7Bfqfqgw/FTYwYme+Jw/fqQGp8eF9DYW+qV/tiOOGAEeHSWopKFU/E
i91q0GNVDvprKbkfcamSKAsaSZ7KJWhU7yhzdwnVs73rAVGaTuQlthwSNDJqQ4M8
O7hCBZRPCnVnwOfjUBEwbk4WHqi0hMtcHOP1ESRQ+TF9LTd0Fcavn8siq9KyfGJv
WEnjvGdgldE5SwHLd7pP59hkQTNlrvLu0WKgchDwb9POI+a8XOZClpYwaJh+giBf
xahHcYPV2Jhb7AFplNb7hIVRkmZEqCpNsw0WQwIDAQABAoIBAB2u7ZLheKCY71Km
bhKYqnKb6BmxgfNfqmq4858p07/kKG2O+Mg1xooFgHrhUhwuKGbCPee/kNGNrXeF
pFW9JrwOXVS2pnfaNw6ObUWhuvhLaxgrhqLAdoUEgWoYOHcKzs3zhj8Gf6di+edq
SyTA8+xnUtVZ6iMRKvP4vtCUqaIgBnXdmQbGINP+/4Qhb5R7XzMt/xPe6uMyAIyC
y5Fm9HnvekaepaeFEf3bh4NV1iN/R8px6cFc6ELYxIZc/4Xbm91WGqSdB0iSriaZ
TjgrmaFjSO40tkCaxI9N6DGzJpmpnMn07ifhl2VjnGOYwtyuh6MKEnyLqTrTg9x0
i3mMwskCgYEA9IyljPRerXxHUAJt+cKOayuXyNt80q9PIcGbyRNvn7qIY6tr5ut+
ZbaFgfgHdSJ/4nICRq02HpeDJ8oj9BmhTAhcX6c1irH5ICjRlt40qbPwemIcpybt
mb+DoNYbI8O4dUNGH9IPfGK8dRpOok2m+ftfk94GmykWbZF5CnOKIp8CgYEA2Syc
5xlKB5Qk2ZkwXIzxbzozSfunHhWWdg4lAbyInwa6Y5GB35UNdNWI8TAKZsN2fKvX
RFgCjbPreUbREJaM3oZ92o5X4nFxgjvAE1tyRqcPVbdKbYZgtcqqJX06sW/g3r/3
RH0XPj2SgJIHew9sMzjGWDViMHXLmntI8rVA7d0CgYBOr36JFwvrqERN0ypNpbMr
epBRGYZVSAEfLGuSzEUrUNqXr019tKIr2gmlIwhLQTmCxApFcXArcbbKs7jTzvde
PoZyZJvOr6soFNozP/YT8Ijc5/quMdFbmgqhUqLS5CPS3z2N+YnwDNj0mO1aPcAP
STmcm2DmxdaolJksqrZ0owKBgQCD0KJDWoQmaXKcaHCEHEAGhMrQot/iULQMX7Vy
gl5iN5E2EgFEFZIfUeRWkBQgH49xSFPWdZzHKWdJKwSGDvrdrcABwdfx520/4MhK
d3y7CXczTZbtN1zHuoTfUE0pmYBhcx7AATT0YCblxrynosrHpDQvIefBBh5YW3AB
cKZCOQKBgEM/ixzI/OVSZ0Py2g+XV8+uGQyC5XjQ6cxkVTX3Gs0ZXbemgUOnX8co
eCXS4VrhEf4/HYMWP7GB5MFUOEVtlLiLM05ruUL7CrphdfgayDXVcTPfk75lLhmu
KAwp3tIHPoJOQiKNQ3/qks5km/9dujUGU2ARiU3qmxLMdgegFz8e
-----END RSA PRIVATE KEY-----
"""


class AACryptor:
    """
    Client-side in-band TLS for Android Auto.
    Mirrors the memory-BIO pattern of aasdk Cryptor.cpp (SSL_set_connect_state).

    The HU is the TLS client; the phone is the TLS server.
    The HU sends ClientHello immediately after VERSION_RESPONSE.

    The public API is intentionally minimal:
        init()                   — create SSL context, set connect state (client)
        write_handshake_input()  — feed bytes received from phone (frame 0x0003 payload)
        drive_handshake()        — advance SSL state machine, return bytes to send
        is_active()              — True once handshake is complete
        encrypt(plaintext)       — returns ciphertext bytes
        decrypt(ciphertext)      — returns plaintext bytes
        deinit()                 — free resources
    """

    def __init__(self) -> None:
        self._ssl_obj:  Optional[ssl.SSLObject] = None
        self._in_bio:   Optional[ssl.MemoryBIO]  = None  # phone → us
        self._out_bio:  Optional[ssl.MemoryBIO]  = None  # us → phone
        self._ctx:      Optional[ssl.SSLContext]  = None
        self._active:   bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def init(self) -> None:
        """Initialise client-side TLS with AA cert+key and memory BIOs.

        Mirrors aasdk Cryptor::init() which calls SSL_set_connect_state.
        The cert+key are loaded so the HU can present them if the phone
        requests client authentication during the handshake.
        """
        import tempfile, os

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as cf:
            cf.write(_AA_CERT_PEM)
            cert_path = cf.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as kf:
            kf.write(_AA_KEY_PEM)
            key_path = kf.name

        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # SSL_set_connect_state
            ctx.check_hostname = False
            ctx.verify_mode    = ssl.CERT_NONE             # phone cert is self-signed
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.maximum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)  # for client-auth
        finally:
            os.unlink(cert_path)
            os.unlink(key_path)

        self._in_bio  = ssl.MemoryBIO()
        self._out_bio = ssl.MemoryBIO()
        self._ssl_obj = ctx.wrap_bio(
            self._in_bio,
            self._out_bio,
            server_side=False,          # HU = TLS client
            server_hostname=None,
        )
        self._ctx    = ctx
        self._active = False
        log.info("AACryptor initialised (TLS 1.2 client, memory BIO)")

    def deinit(self) -> None:
        self._ssl_obj = None
        self._in_bio  = None
        self._out_bio = None
        self._ctx     = None
        self._active  = False

    # ------------------------------------------------------------------
    # Handshake
    # ------------------------------------------------------------------

    def write_handshake_input(self, data: bytes) -> None:
        """Feed bytes from the phone into the SSL read BIO (= Cryptor::writeHandshakeBuffer)."""
        if self._in_bio:
            self._in_bio.write(data)

    def drive_handshake(self) -> bytes:
        """Advance the SSL state machine. Returns bytes to send to the phone.

        On first call (in_bio empty): generates ClientHello immediately.
        On subsequent calls: processes phone reply and generates next round.
        Mirrors Cryptor::doHandshake() + Cryptor::readHandshakeBuffer().
        """
        if self._ssl_obj is None:
            return b""

        if not self._active:
            try:
                self._ssl_obj.do_handshake()
                self._active = True
                log.info("TLS handshake complete")
            except ssl.SSLWantReadError:
                pass  # normal: waiting for more data from phone
            except ssl.SSLError as e:
                log.error("TLS handshake error: %s", e)

        return self._read_out_bio()

    def is_active(self) -> bool:
        return self._active

    # ------------------------------------------------------------------
    # Encrypt / Decrypt (post-handshake)
    # ------------------------------------------------------------------

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt plaintext, return TLS record bytes (= Cryptor::encrypt)."""
        if not self._active or self._ssl_obj is None:
            raise RuntimeError("AACryptor.encrypt called before handshake complete")
        self._ssl_obj.write(plaintext)
        return self._read_out_bio()

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt a TLS record, return plaintext (= Cryptor::decrypt)."""
        if not self._active or self._ssl_obj is None:
            raise RuntimeError("AACryptor.decrypt called before handshake complete")
        self._in_bio.write(ciphertext)
        buf = bytearray()
        try:
            while True:
                chunk = self._ssl_obj.read(4096)
                if not chunk:
                    break
                buf.extend(chunk)
        except ssl.SSLWantReadError:
            pass
        return bytes(buf)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read_out_bio(self) -> bytes:
        """Drain all pending bytes from the SSL write BIO (= Cryptor::readHandshakeBuffer)."""
        if self._out_bio is None:
            return b""
        data = self._out_bio.read()
        return data if data else b""
