#!/usr/bin/env python3
"""
Web Browser Head Unit — TCPServer Module

Cross-platform Priority 2 Core Module extending `BaseBackendModule`.

Module Responsibilities:
  1. Accepts plain TCP connection from phone client on port 5288 (or configured port).
  2. FrameRelay reads raw AA wire frames -> FrameAssembler reassembles multi-frame messages.
  3. Decrypts encrypted frames using `AACryptor` (memory-BIO TLS state machine) once active.
  4. Publishes `aa.frame.ch<N>` target events directly to destination channel modules.
  5. Implements multi-frame input lock on `on_frame_send` to ensure TLS state safety.
  6. Exposes REST API endpoints (`GET /api/tcp/status`, `POST /api/tcp/restart`).
"""

import asyncio
import os
import struct
import threading
import time
from pathlib import Path
from typing import Any, Optional

from aiohttp import web

from shared.base_module import BaseBackendModule, run_module
from shared.config_schema import field_bool, field_int, field_string
from shared.media_shm import BidirectionalMediaSHM
from modules.tcp_server.aa_cryptor import AACryptor
from modules.tcp_server.frame_codec import FrameAssembler, encode
from modules.tcp_server.frame_relay import FrameRelay
from modules.tcp_server.message_to_proto import frame_data_to_dict
from modules.tcp_server.server import TCPServer

_FLAG_ENCRYPTED = 0x08
_MSG_SHUTDOWN_REQUEST = 0x000D
_MSG_SHUTDOWN_RESPONSE = 0x000E
_SHUTDOWN_ACK_TIMEOUT = 3.0


class TCPServerModule(BaseBackendModule):
    def __init__(self):
        super().__init__(
            name="tcp_server",
            priority=3,
            path_prefix="/api/tcp",
        )
        self._server: Optional[TCPServer] = None
        self._relay: Optional[FrameRelay] = None
        self._cryptor: Optional[AACryptor] = None
        self._assembler: Optional[FrameAssembler] = None
        self._server_thread: Optional[threading.Thread] = None
        self._shm = BidirectionalMediaSHM(create=True)

        self._server_starting = False
        self._server_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._crypto_lock = threading.RLock()
        self._multi_frame_lock = threading.Lock()

        self._restart_pending = False
        self._shutdown_ack_event = threading.Event()

        # Telemetry counters
        self._client_address: Optional[str] = None
        self._frames_received_count = 0
        self._frames_sent_count = 0

    def get_default_config(self) -> dict[str, Any]:
        return {
            "host": "0.0.0.0",
            "port": 5288,
            "autostart": True,
            "publish_full_frame": False,
        }

    def get_schema(self) -> dict[str, Any]:
        return {
            "host": field_string(default="0.0.0.0"),
            "port": field_int(default=5288, min=1024, max=65535),
            "autostart": field_bool(default=True),
            "publish_full_frame": field_bool(default=False),
        }

    async def setup(self) -> None:
        """Registers REST routes and ZMQ topic subscriptions."""
        self.add_http_route("GET", "/api/tcp/status", self.handle_get_status)
        self.add_http_route("POST", "/api/tcp/restart", self.handle_post_restart)

        self.subscribe("aa.frame.send", self.on_frame_send)
        self.subscribe("aa.handshake.start_tls", self.on_handshake_start_tls)
        self.subscribe("aa.handshake.feed_input", self.on_handshake_feed_input)
        self.subscribe("aa.session.restart", self.on_aa_session_restart)
        self.subscribe("aa.frame.ch0", self.on_ch0_frame)

    async def run(self) -> None:
        """Main module execution loop. Handles optional autostart mode."""
        if self.config.get("autostart", True):
            self.log.info("Autostart configured — initializing TCP server listener...")
            self.start_tcp_server()

        while self._running:
            await asyncio.sleep(1.0)

    def start_tcp_server(self) -> None:
        with self._server_lock:
            if self._server_starting or self._server is not None:
                self.log.info("TCP server is already running/starting — ignoring start request")
                return
            self._server_starting = True

        t = threading.Thread(target=self._run_server_thread, daemon=True, name="tcp_server_listener")
        self._server_thread = t
        t.start()

    def _run_server_thread(self) -> None:
        host = self.config.get("host", "0.0.0.0")
        port = self.config.get("port", 5288)

        server = TCPServer(host=host, port=port)
        with self._server_lock:
            self._server = server

        if not server.start():
            with self._server_lock:
                if self._server is server:
                    self._server = None
                self._server_starting = False
            self.publish("tcp.server.error", {"error": "TCPServer.start() failed"})
            return

        with self._server_lock:
            self._server_starting = False

        self.log.info(f"TCPServer listening on {server.host}:{server.port}")
        self.publish("tcp.server.started", {"host": server.host, "port": server.port})

        result = server.accept()
        if result is None:
            self.publish("tcp.server.error", {"error": "No connection within timeout"})
            self._teardown_server()
            return

        conn, address = result
        self._client_address = str(address)
        self.log.info(f"Phone client connected: {address}")
        self.publish("tcp.session.connected", {"address": address})

        with self._crypto_lock:
            self._cryptor = None

        self._assembler = FrameAssembler()
        self._relay = FrameRelay(
            sock=conn,
            on_frame_cb=self._on_raw_frame,
            on_closed_cb=self._on_session_closed,
        )
        self._relay.start()

    def _on_raw_frame(self, channel_id: int, flags: int, payload: bytes, total_size: int) -> None:
        """Callback from FrameRelay for every raw frame read off socket."""
        frame_type = flags & 0x03

        # Acquire multi-frame lock for intermediate chunks to lock on_frame_send during multi-packet reception
        if frame_type != 0x03:  # Not single bulk frame
            self._multi_frame_lock.acquire(timeout=2.0)

        try:
            result = self._assembler.feed(channel_id, flags, payload, total_size)
            if result is None:
                return

            channel_id, flags, assembled, total_size = result
            encrypted = bool(flags & _FLAG_ENCRYPTED)

            if encrypted:
                try:
                    with self._crypto_lock:
                        if self._cryptor is not None and self._cryptor.is_active():
                            assembled = self._cryptor.decrypt(assembled)
                except Exception as exc:
                    self.log.error(f"_on_raw_frame: Decrypt failed ch={channel_id} — {exc}")
                    return

            if len(assembled) < 2:
                self.log.error(f"_on_raw_frame: ch={channel_id} payload too short ({len(assembled)} bytes)")
                return

            message_id = struct.unpack_from(">H", assembled, 0)[0]
            body = assembled[2:]
            self._frames_received_count += 1

            # Write media channel frames (Video ch 2, Audio ch 3/4) to SHM zero-copy
            if channel_id in (2, 3, 4):
                stream_type = 0 if channel_id == 2 else 1
                shm_offset = self._shm.downstream.write_frame(stream_type, 0, body)
                self.publish(
                    "aa.frame.shm",
                    {
                        "channel_id": channel_id,
                        "message_id": message_id,
                        "encrypted": encrypted,
                        "shm_offset": shm_offset,
                        "payload_len": len(body),
                    },
                )

            frame_data = {
                "channel_id": channel_id,
                "message_id": message_id,
                "encrypted": encrypted,
                "payload_hex": body.hex(),
            }
            received_data = {
                "channel_id": channel_id,
                "message_id": message_id,
                "encrypted": encrypted,
                "payload_len": len(body),
                "payload_head": body[:16].hex(),
            }
            if self.config.get("publish_full_frame", False):
                received_data["payload_hex"] = frame_data["payload_hex"]

            self.publish("aa.frame.received", received_data)
            # Targeted channel routing: aa.frame.ch<N>
            self.publish(f"aa.frame.ch{channel_id}", frame_data)

        finally:
            if self._multi_frame_lock.locked():
                try:
                    self._multi_frame_lock.release()
                except RuntimeError:
                    pass

    def on_frame_send(self, topic: str, payload: dict) -> None:
        """Write AA frame to socket. Blocked during active multi-frame input reception."""
        relay = self._relay
        if relay is None:
            return

        try:
            channel_id = int(payload["channel_id"])
            message_id = int(payload["message_id"])
            body = bytes.fromhex(payload["payload_hex"])
            ssl_active = bool(payload.get("encrypted", False))
        except (KeyError, ValueError) as exc:
            self.log.error(f"on_frame_send: Malformed payload — {exc}")
            return

        # Acquire multi-frame lock to ensure on_frame_send waits if inbound multi-frame reception is in progress
        with self._multi_frame_lock:
            try:
                with self._crypto_lock:
                    frames = encode(
                        channel_id=channel_id,
                        message_id=message_id,
                        body=body,
                        ssl_active=ssl_active,
                        cryptor=self._cryptor,
                    )
            except Exception as exc:
                self.log.error(f"on_frame_send: Encode failed ch={channel_id} msg=0x{message_id:04x} — {exc}")
                return

            try:
                with self._write_lock:
                    for frame in frames:
                        relay.send_raw(frame)
                self._frames_sent_count += len(frames)
            except Exception as exc:
                self.log.error(f"on_frame_send: Socket write failed — {exc}")

    def on_handshake_completed(self, topic: str, payload: dict) -> None:
        device_address = payload.get("device_address", "")
        phone_ip = payload.get("phone_ip", "")
        self.log.info(f"Handshake completed from {device_address} (phone_ip={phone_ip}) — starting TCP server")
        self.start_tcp_server()

    def on_handshake_start_tls(self, topic: str, payload: dict) -> None:
        self.log.info("aa.handshake.start_tls — initializing AACryptor")
        with self._crypto_lock:
            self._cryptor = AACryptor()
            self._cryptor.init()
            outgoing = self._cryptor.drive_handshake()
        if outgoing:
            self.publish("tcp.server.tls_handshake", {"outgoing_hex": outgoing.hex()})

    def on_handshake_feed_input(self, topic: str, payload: dict) -> None:
        if self._cryptor is None:
            return

        try:
            data = bytes.fromhex(payload["payload_hex"])
        except (KeyError, ValueError) as exc:
            self.log.error(f"on_handshake_feed_input: Malformed payload — {exc}")
            return

        with self._crypto_lock:
            self._cryptor.write_handshake_input(data)
            outgoing = self._cryptor.drive_handshake()
            active = self._cryptor.is_active()

        if active:
            self.log.info("TLS handshake complete — publishing tcp.server.tls_handshake_completed")
            self.publish("tcp.server.tls_handshake_completed", {})
        elif outgoing:
            self.publish("tcp.server.tls_handshake", {"outgoing_hex": outgoing.hex()})

    def on_aa_session_restart(self, topic: str, payload: dict) -> None:
        if self._relay is None:
            return

        self.log.info("aa.session.restart — sending SHUTDOWN_REQUEST to phone")
        self._restart_pending = True
        self._shutdown_ack_event.clear()

        with self._crypto_lock:
            shutdown_frames = encode(
                channel_id=0,
                message_id=_MSG_SHUTDOWN_REQUEST,
                body=b"",
                ssl_active=(self._cryptor is not None and self._cryptor.is_active()),
                cryptor=self._cryptor,
            )
        try:
            with self._write_lock:
                for frame in shutdown_frames:
                    self._relay.send_raw(frame)
        except Exception as exc:
            self.log.error(f"on_aa_session_restart: Failed to send SHUTDOWN_REQUEST — {exc}")
            self._restart_pending = False
            return

        acked = self._shutdown_ack_event.wait(timeout=_SHUTDOWN_ACK_TIMEOUT)
        if not acked:
            self.log.warning(f"on_aa_session_restart: SHUTDOWN_RESPONSE timeout — proceeding")

        if self._cryptor is not None:
            with self._crypto_lock:
                self._cryptor.deinit()

        if self._assembler is not None:
            self._assembler.reset()

        self._restart_pending = False
        self.publish("aa.session.restarting", {})

    def on_ch0_frame(self, topic: str, payload: dict) -> None:
        if not self._restart_pending:
            return
        if int(payload.get("message_id", -1)) == _MSG_SHUTDOWN_RESPONSE:
            self._shutdown_ack_event.set()

    def _on_session_closed(self) -> None:
        if self._restart_pending:
            return
        self.log.info("AA TCP session closed")
        self.publish("tcp.session.closed", {})
        self._teardown_server()

    def _teardown_server(self) -> None:
        if self._relay:
            self._relay.stop()
            self._relay = None
        if self._server:
            self._server.stop()
            self._server = None
        if self._cryptor:
            with self._crypto_lock:
                self._cryptor.deinit()
                self._cryptor = None
        if self._assembler:
            self._assembler.reset()
            self._assembler = None
        with self._server_lock:
            self._server_starting = False
        self._client_address = None

    async def handle_get_status(self, request: web.Request) -> web.Response:
        """REST endpoint GET /api/tcp/status."""
        tls_active = (self._cryptor is not None and self._cryptor.is_active()) if self._cryptor else False
        return web.json_response({
            "status": "ok",
            "server_running": self._server is not None and self._server._running,
            "host": self.config.get("host", "0.0.0.0"),
            "port": self.config.get("port", 5288),
            "client_address": self._client_address,
            "tls_active": tls_active,
            "frames_received": self._frames_received_count,
            "frames_sent": self._frames_sent_count,
        })

    async def handle_post_restart(self, request: web.Request) -> web.Response:
        """REST endpoint POST /api/tcp/restart."""
        self.on_aa_session_restart("aa.session.restart", {})
        return web.json_response({"status": "ok", "message": "Restart sequence initiated"})

    async def teardown(self) -> None:
        """Module teardown on orchestrator shutdown."""
        self.log.info("Teardown TCPServerModule...")
        self._teardown_server()
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=2.0)


if __name__ == "__main__":
    run_module(TCPServerModule)
