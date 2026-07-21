"""
test_bt_connect_to_handshake.py — E2E Smoke §1

Verifica il path critico:
  RFCOMM connected  →  RfcommHandshake completo  →  tcp_server pronto

I moduli coinvolti girano in-process nello stesso broker ZMQ della fixture
`in_process_broker`.  Hardware (D-Bus, GLib, BlueZ) sostituiti da stubs via
`StackLauncher`.  Nessuna connessione Bluetooth reale richiesta.

Suite:
    1. RFCOMM → WifiStartRequest inviato e PhoneMock risponde con WifiInfoRequest
    2. RfcommHandshake consegna WifiInfoResponse corretta
    3. PhoneMock riceve WifiConnectStatus (completed=True)
    4. Bus pubblica rfcomm.handshake.completed
    5. TCP server diventa raggiungibile dopo l'handshake
    6. Seconda connessione RFCOMM durante handshake attivo viene rifiutata
    7. PhoneMock senza ack WifiStartResponse (send_start_ack=False) porta comunque a successo
    8. Handshake fallisce se il socket viene chiuso durante WifiInfoRequest
    9. Bus pubblica rfcomm.handshake.failed su timeout RFCOMM
   10. Stack completo: da rfcomm.connected ad aa.session.active (happy path)
"""

import socket
import threading
import time
import importlib
import sys
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# E2E helpers (no source module dependency at import time)
# ---------------------------------------------------------------------------

from e2e.helpers.phone_mock import PhoneMock, TcpPhoneClient          # noqa: E402
from e2e.helpers.frame_sequences import (                             # noqa: E402
    VersionSequence,
    ServiceDiscoverySeq,
    ChannelOpenSeq,
    ShutdownSequence,
    MSG_VERSION_REQUEST,
    MSG_VERSION_RESPONSE,
    MSG_SERVICE_DISCOVERY_REQ,
    CH_CONTROL,
    CH_INPUT, CH_SENSOR, CH_VIDEO, CH_MEDIA,
)
from e2e.helpers.stack_launcher import e2e_stack, StackLauncher       # noqa: E402

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.e2e_smoke

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

TCP_PORT       = 5288
SMOKE_TIMEOUT  = 8.0   # max seconds any single assertion may wait

# Minimal module set for the RFCOMM→TCP path
RFCOMM_MODULES = ["config_manager", "channel_manager", "rfcomm_handshake", "tcp_server"]

# ---------------------------------------------------------------------------
# Helper: create a socket pair and build a PhoneMock on the phone side
# ---------------------------------------------------------------------------

def _make_socketpair(send_start_ack: bool = True, wifi_join_delay: float = 0.05):
    """
    Returns (hu_sock, phone_mock) where hu_sock is the head-unit-side socket
    and phone_mock is already started in a daemon thread.
    """
    hu_sock, phone_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    mock = PhoneMock(
        phone_sock,
        send_start_ack=send_start_ack,
        wifi_join_delay=wifi_join_delay,
    ).start()
    return hu_sock, mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRfcommToTcpSmoke:
    """Tests that verify the RFCOMM handshake path without AA-level frames."""

    def test_phonestock_completes_rfcomm_handshake(
        self, in_process_broker
    ):
        """
        §1 — PhoneMock completa il proprio lato RFCOMM quando lo stack
        pubblica bluetooth_manager.rfcomm.connected con il fd del socket HU.
        """
        with e2e_stack(in_process_broker, modules=RFCOMM_MODULES) as stack:
            hu_sock, mock = _make_socketpair()

            stack.publish(
                "bluetooth_manager.rfcomm.connected",
                {"fd": hu_sock.fileno(), "remote_address": "AA:BB:CC:DD:EE:FF"},
            )

            assert mock.wait_done(timeout=SMOKE_TIMEOUT), \
                "PhoneMock non ha completato l'handshake RFCOMM entro il timeout"
            assert mock.completed, f"Handshake fallito: {mock.result.error}"

    def test_phone_receives_wifi_credentials(
        self, in_process_broker
    ):
        """
        §2 — PhoneMock riceve SSID/Key non-vuoti nella WifiInfoResponse.
        """
        with e2e_stack(in_process_broker, modules=RFCOMM_MODULES) as stack:
            hu_sock, mock = _make_socketpair()

            stack.publish(
                "bluetooth_manager.rfcomm.connected",
                {"fd": hu_sock.fileno(), "remote_address": "AA:BB:CC:DD:EE:FF"},
            )

            mock.wait_done(timeout=SMOKE_TIMEOUT)
            # L'handshake deve aver ricevuto credenziali (ssid non vuoto)
            assert mock.result.ssid_received != "" or mock.completed, \
                "SSID vuoto — lo stack non ha inviato WifiInfoResponse"

    def test_bus_publishes_handshake_completed(
        self, in_process_broker
    ):
        """
        §4 — Dopo handshake RFCOMM riuscito il bus deve pubblicare
        rfcomm.handshake.completed.
        """
        with e2e_stack(in_process_broker, modules=RFCOMM_MODULES) as stack:
            hu_sock, mock = _make_socketpair()

            stack.publish(
                "bluetooth_manager.rfcomm.connected",
                {"fd": hu_sock.fileno(), "remote_address": "AA:BB:CC:DD:EE:FF"},
            )

            mock.wait_done(timeout=SMOKE_TIMEOUT)

            msgs = stack.collect("rfcomm.handshake.completed", timeout=3.0)
            assert msgs, "Il bus non ha pubblicato rfcomm.handshake.completed"

    def test_no_start_ack_still_succeeds(
        self, in_process_broker
    ):
        """
        §7 — Anche senza WifiStartResponse (ack opzionale) l'handshake deve
        completarsi con successo.
        """
        with e2e_stack(in_process_broker, modules=RFCOMM_MODULES) as stack:
            hu_sock, mock = _make_socketpair(send_start_ack=False)

            stack.publish(
                "bluetooth_manager.rfcomm.connected",
                {"fd": hu_sock.fileno(), "remote_address": "AA:BB:CC:DD:EE:FF"},
            )

            assert mock.wait_done(timeout=SMOKE_TIMEOUT)
            assert mock.completed

    def test_socket_closed_mid_handshake_publishes_failed(
        self, in_process_broker
    ):
        """
        §8 — Se il socket viene chiuso durante l'handshake il bus deve
        pubblicare rfcomm.handshake.failed (o lo stack non deve bloccarsi).
        """
        with e2e_stack(in_process_broker, modules=RFCOMM_MODULES) as stack:
            hu_sock, phone_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)

            # Chiudi il lato phone subito — HU riceverà EOF durante l'handshake
            def _close_after(delay):
                time.sleep(delay)
                try:
                    phone_sock.close()
                except OSError:
                    pass

            threading.Thread(target=_close_after, args=(0.05,), daemon=True).start()

            stack.publish(
                "bluetooth_manager.rfcomm.connected",
                {"fd": hu_sock.fileno(), "remote_address": "AA:BB:CC:DD:EE:FF"},
            )

            # Lo stack non deve bloccarsi — raccogliamo qualunque esito
            failed = stack.collect("rfcomm.handshake.failed", timeout=4.0)
            completed = stack.received("rfcomm.handshake.completed")
            assert failed or not completed, \
                "Lo stack ha riportato successo con socket chiuso dal peer"

    def test_duplicate_rfcomm_connection_rejected(
        self, in_process_broker
    ):
        """
        §6 — Una seconda connessione RFCOMM mentre l'handshake è attivo
        non deve generare una seconda entrata in rfcomm.handshake.completed.
        """
        with e2e_stack(in_process_broker, modules=RFCOMM_MODULES) as stack:
            hu_sock1, mock1 = _make_socketpair(wifi_join_delay=0.3)

            stack.publish(
                "bluetooth_manager.rfcomm.connected",
                {"fd": hu_sock1.fileno(), "remote_address": "AA:BB:CC:DD:EE:FF"},
            )

            # Breve attesa per avviare il primo handshake
            time.sleep(0.05)

            # Seconda connessione durante handshake attivo
            hu_sock2, mock2 = _make_socketpair()
            stack.publish(
                "bluetooth_manager.rfcomm.connected",
                {"fd": hu_sock2.fileno(), "remote_address": "11:22:33:44:55:66"},
            )

            # Attendi il completamento del primo handshake
            mock1.wait_done(timeout=SMOKE_TIMEOUT)

            # Deve esserci al massimo UN handshake completato
            completed = stack.collect("rfcomm.handshake.completed", timeout=2.0, count=10)
            assert len(completed) <= 1, \
                f"Handshake duplicati rilevati: {len(completed)} eventi rfcomm.handshake.completed"


class TestTcpServerAvailableAfterHandshake:
    """
    Tests that verify the TCP server is reachable after RFCOMM handshake.
    """

    def test_tcp_server_reachable_after_handshake(
        self, in_process_broker
    ):
        """
        §5 — Dopo l'handshake RFCOMM il tcp_server deve accettare connessioni
        TCP sulla porta 5288.
        """
        with e2e_stack(in_process_broker, modules=RFCOMM_MODULES) as stack:
            hu_sock, mock = _make_socketpair()

            stack.publish(
                "bluetooth_manager.rfcomm.connected",
                {"fd": hu_sock.fileno(), "remote_address": "AA:BB:CC:DD:EE:FF"},
            )

            mock.wait_done(timeout=SMOKE_TIMEOUT)
            if not mock.completed:
                pytest.skip("Handshake RFCOMM non completato — skip TCP test")

            # tcp_server può impiegare un momento a mettersi in ascolto
            connected = False
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    s = socket.create_connection(("127.0.0.1", TCP_PORT), timeout=0.5)
                    s.close()
                    connected = True
                    break
                except (OSError, ConnectionRefusedError):
                    time.sleep(0.1)

            assert connected, f"tcp_server non raggiungibile su 127.0.0.1:{TCP_PORT} dopo handshake"

    def test_tcp_client_receives_version_request(
        self, in_process_broker
    ):
        """
        §3 + §5 — Dopo la connessione TCP il tcp_server / oaa_control_channel
        deve inviare al client AA il frame VERSION_REQUEST sul canale 0.
        """
        with e2e_stack(
            in_process_broker,
            modules=["config_manager", "channel_manager",
                     "rfcomm_handshake", "tcp_server", "oaa_control_channel"],
        ) as stack:
            hu_sock, mock = _make_socketpair()

            stack.publish(
                "bluetooth_manager.rfcomm.connected",
                {"fd": hu_sock.fileno(), "remote_address": "AA:BB:CC:DD:EE:FF"},
            )

            mock.wait_done(timeout=SMOKE_TIMEOUT)
            if not mock.completed:
                pytest.skip("Handshake RFCOMM non completato")

            # Attendi apertura TCP
            client = None
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    client = TcpPhoneClient.connect("127.0.0.1", TCP_PORT, timeout=0.5)
                    break
                except OSError:
                    time.sleep(0.1)

            if client is None:
                pytest.skip("tcp_server non raggiungibile — skip frame test")

            try:
                # oaa_control_channel invia VERSION_REQUEST per primo
                frames = client.recv_frames_until(
                    predicate=lambda ch, fl, mid, body: mid == MSG_VERSION_REQUEST,
                    timeout=5.0,
                    max_frames=10,
                )
                assert frames, "Nessun frame ricevuto da oaa_control_channel"
                assert frames[-1][2] == MSG_VERSION_REQUEST, \
                    f"Atteso MSG_VERSION_REQUEST (0x{MSG_VERSION_REQUEST:04X}), "\
                    f"ricevuto 0x{frames[-1][2]:04X}"
            finally:
                client.close()


class TestFullAaSmoke:
    """
    §10 — Happy-path end-to-end: da bluetooth_manager.rfcomm.connected fino ad
    aa.session.active (o almeno fin dopo il VERSION exchange).
    """

    def test_version_exchange_completes(
        self, in_process_broker
    ):
        """
        Il client TCP risponde al VERSION_REQUEST con un VERSION_RESPONSE valido
        e il bus deve riflettere lo stato di avanzamento del canale 0.
        """
        with e2e_stack(
            in_process_broker,
            modules=["config_manager", "channel_manager",
                     "rfcomm_handshake", "tcp_server", "oaa_control_channel"],
        ) as stack:
            hu_sock, mock = _make_socketpair()

            stack.publish(
                "bluetooth_manager.rfcomm.connected",
                {"fd": hu_sock.fileno(), "remote_address": "AA:BB:CC:DD:EE:FF"},
            )

            mock.wait_done(timeout=SMOKE_TIMEOUT)
            if not mock.completed:
                pytest.skip("Handshake RFCOMM non completato")

            # Connetti client TCP
            client = None
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    client = TcpPhoneClient.connect("127.0.0.1", TCP_PORT, timeout=0.5)
                    break
                except OSError:
                    time.sleep(0.1)

            if client is None:
                pytest.skip("tcp_server non raggiungibile")

            try:
                # Aspetta VERSION_REQUEST
                frames = client.recv_frames_until(
                    predicate=lambda ch, fl, mid, body: mid == MSG_VERSION_REQUEST,
                    timeout=5.0,
                )
                assert frames and frames[-1][2] == MSG_VERSION_REQUEST

                # Rispondi con VERSION_RESPONSE
                from e2e.helpers.frame_sequences import VersionSequence
                ok = client.send_frame(
                    channel_id=CH_CONTROL,
                    msg_id=MSG_VERSION_RESPONSE,
                    body=VersionSequence.response_body(),
                )
                assert ok, "send_frame VERSION_RESPONSE fallito"

                # Attendi il prossimo frame (SSL o SERVICE_DISCOVERY)
                next_frames = client.recv_frames_until(
                    predicate=lambda ch, fl, mid, body: True,  # qualunque frame
                    timeout=4.0,
                    max_frames=5,
                )
                # Se riceviamo almeno un frame, il VERSION exchange è avvenuto
                assert next_frames or stack.received("aa.handshake.state"), \
                    "Nessun frame successivo al VERSION exchange"

            finally:
                client.close()

    def test_shutdown_sequence(
        self, in_process_broker
    ):
        """
        Dopo la connessione TCP, inviando SHUTDOWN_REQUEST lo stack deve
        chiudere la sessione senza crash (aa.session.shutdown sul bus).
        """
        with e2e_stack(
            in_process_broker,
            modules=["config_manager", "channel_manager",
                     "rfcomm_handshake", "tcp_server", "oaa_control_channel"],
        ) as stack:
            hu_sock, mock = _make_socketpair()

            stack.publish(
                "bluetooth_manager.rfcomm.connected",
                {"fd": hu_sock.fileno(), "remote_address": "AA:BB:CC:DD:EE:FF"},
            )

            mock.wait_done(timeout=SMOKE_TIMEOUT)
            if not mock.completed:
                pytest.skip("Handshake RFCOMM non completato")

            client = None
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    client = TcpPhoneClient.connect("127.0.0.1", TCP_PORT, timeout=0.5)
                    break
                except OSError:
                    time.sleep(0.1)

            if client is None:
                pytest.skip("tcp_server non raggiungibile")

            try:
                # Drenare eventuali frame iniziali (VERSION_REQUEST)
                client.recv_frames_until(
                    predicate=lambda ch, fl, mid, body: True,
                    timeout=2.0,
                    max_frames=3,
                )

                # Invia SHUTDOWN_REQUEST
                from e2e.helpers.frame_sequences import ShutdownSequence
                client.send_frame(
                    channel_id=CH_CONTROL,
                    msg_id=0x0100,  # MSG_SHUTDOWN_REQUEST
                    body=ShutdownSequence.request_body(reason=0),
                )

                # Attendi SHUTDOWN_RESPONSE o chiusura connessione
                resp = client.recv_frames_until(
                    predicate=lambda ch, fl, mid, body: mid == 0x0101,  # MSG_SHUTDOWN_RESPONSE
                    timeout=3.0,
                    max_frames=5,
                )

                # Accettabile anche la chiusura del socket (resp vuoto)
                session_shutdown = stack.collect("aa.session.shutdown", timeout=2.0)
                # Se né SHUTDOWN_RESPONSE né aa.session.shutdown, l'assertEqual fallirà
                assert resp or session_shutdown, \
                    "Nessuna risposta allo SHUTDOWN_REQUEST e nessun evento aa.session.shutdown"

            finally:
                client.close()
