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
from collections import deque
import os
import struct
import threading
import time
from pathlib import Path
from typing import Any, Optional, Dict

from aiohttp import web

from shared.base_module import BaseBackendModule, run_module
from shared.config_schema import field_bool, field_int, field_string
from shared.media_shm import BidirectionalMediaSHM
from shared.proto_utils import parse_media_with_timestamp
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from modules.tcp_server.aa_cryptor import AACryptor
from modules.tcp_server.frame_codec import FrameAssembler, encode
from modules.tcp_server.frame_relay import FrameRelay
from modules.tcp_server.server import TCPServer
from modules.tcp_server.messages_logging_levels import CHANNEL_MESSAGES_DEBUG_LEVELS

from protos.oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage

_FLAG_ENCRYPTED = 0x08
_MSG_SHUTDOWN_REQUEST = ControlMessage.Enum.SHUTDOWN_REQUEST
_MSG_SHUTDOWN_RESPONSE = ControlMessage.Enum.SHUTDOWN_RESPONSE
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

        # Rolling history buffers for diagnostic state dumps
        self._recent_sent_frames: deque = deque(maxlen=20)
        self._recent_recv_raw_frames: deque = deque(maxlen=20)
        self._recent_recv_processed_frames: deque = deque(maxlen=20)

        # Dynamic channel type mapping (pre-populated with standards, updated by SDR)
        self.channel_type_map: Dict[int, str] = {
            0: "CONTROL",
            1: "INPUT",
            2: "SENSOR",
            3: "VIDEO",
            4: "AUDIO",
            5: "AUDIO",
            6: "AUDIO",
            7: "AUDIO_MIC",
        }


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
        self.subscribe("aa.sdr.channels", self.on_sdr_channels)

    async def on_sdr_channels(self, data: dict) -> None:
        type_map = data.get("type_map", {})
        self.channel_type_map = {int(k): v for k, v in type_map.items() if str(k).isdigit()}
        self.log.info(f"tcp_server: Received dynamic channel type map from SDR: {self.channel_type_map}")


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
                self.log.info(f"🔄 [TCP Server State] Start requested but server is already active (starting={self._server_starting}, server={self._server is not None}, client={self._client_address})")
                return
            self._server_starting = True

        self.log.info("🔄 [TCP Server State] Launching new TCP server listener thread...")
        t = threading.Thread(target=self._run_server_thread, daemon=True, name="tcp_server_listener")
        self._server_thread = t
        t.start()

    def _run_server_thread(self) -> None:
        host = self.config.get("host", "0.0.0.0")
        port = self.config.get("port", 5288)

        self.log.info(f"🔄 [TCP Server State] Binding socket for host='{host}', port={port}...")
        server = TCPServer(host=host, port=port)
        with self._server_lock:
            self._server = server

        if not server.start():
            with self._server_lock:
                if self._server is server:
                    self._server = None
                self._server_starting = False
            self.log.error("❌ [TCP Server State] TCPServer.start() failed to bind port")
            self.publish("tcp.server.error", {"error": "TCPServer.start() failed"})
            return

        with self._server_lock:
            self._server_starting = False

        self.log.info(f"🌐 [TCP Stage 4/5] READY & LISTENING on {server.host}:{server.port} — awaiting phone connection...")
        self.publish("tcp.server.started", {"host": server.host, "port": server.port})

        result = server.accept()
        if result is None:
            self.log.warning("⚠️ [TCP Server State] TCP accept timed out or returned None — shutting down server thread")
            self.publish("tcp.server.error", {"error": "No connection within timeout"})
            self._teardown_server()
            return

        conn, address = result
        self._client_address = str(address)
        self._logged_first_encrypted = False
        self._logged_first_tcp_bytes = False
        self.log.info(f"🌐 [TCP Stage 4/5] 🎉 Phone client connected successfully from {address}!")
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
        ts = time.strftime("%H:%M:%S", time.localtime()) + f".{int(time.time()*1000)%1000:03d}"
        self._recent_recv_raw_frames.append({
            "ts": ts,
            "ch": channel_id,
            "flags": f"0x{flags:02x}",
            "ftype": flags & 0x03,
            "len": len(payload),
            "total_size": total_size,
            "head_hex": payload[:16].hex() if payload else "",
        })

        if not getattr(self, "_logged_first_tcp_bytes", False):
            self._logged_first_tcp_bytes = True
            self.log.info(f"🌐 [TCP Stage 5/5] First raw bytes received (hex): {payload[:128].hex()}")

        encrypted = bool(flags & _FLAG_ENCRYPTED)

        # EXPERIMENTAL FIX: Decrypt raw frames immediately in TCP arrival order before FrameAssembler reassembly
        if encrypted:
            try:
                with self._crypto_lock:
                    if self._cryptor is not None and self._cryptor.is_active():
                        payload = self._cryptor.decrypt(payload)
                        if not getattr(self, "_logged_first_encrypted", False):
                            self._logged_first_encrypted = True
                            msg_id_preview = struct.unpack_from(">H", payload, 0)[0] if len(payload) >= 2 else 0
                            self.log.info(f"🔑 [TLS Stage 5/5] 🔓 FIRST ENCRYPTED MESSAGE RECEIVED & DECRYPTED! (ch={channel_id}, msg=0x{msg_id_preview:04x}, len={len(payload)} bytes)")
            except Exception as exc:
                tls_info = self._cryptor.parse_tls_record_header(payload) if self._cryptor else {}
                in_pending = self._cryptor.get_in_bio_pending() if self._cryptor else 0
                out_pending = self._cryptor.get_out_bio_pending() if self._cryptor else 0
                assembler_state = self._assembler.get_debug_state() if self._assembler else {}

                recent_sent = list(self._recent_sent_frames)[-5:]
                recent_raw = list(self._recent_recv_raw_frames)[-5:]
                recent_proc = list(self._recent_recv_processed_frames)[-5:]

                self.log.error(
                    f"❌ [TLS Decrypt Error Dump - EXPERIMENTAL] Decrypt failed ch={channel_id} — {exc}\n"
                    f"   ├─ Failing Frame: ch={channel_id}, flags=0x{flags:02x} (ftype={flags & 0x03}), payload_len={len(payload)}\n"
                    f"   ├─ TLS Record Header Parse: {tls_info}\n"
                    f"   ├─ Payload Preview: head={payload[:32].hex()}, tail={payload[-16:].hex() if len(payload) > 16 else ''}\n"
                    f"   ├─ Cryptor State: active={self._cryptor.is_active() if self._cryptor else False}, in_bio_pending={in_pending}, out_bio_pending={out_pending}\n"
                    f"   ├─ Assembler State: {assembler_state}\n"
                    f"   ├─ Recent Sent (last 5): {recent_sent}\n"
                    f"   ├─ Recent Raw Recv (last 5): {recent_raw}\n"
                    f"   └─ Recent Processed Recv (last 5): {recent_proc}"
                )
                return

        result = self._assembler.feed(channel_id, flags, payload, total_size)
        if result is None:
            return

        channel_id, flags, assembled, total_size = result

        if len(assembled) < 2:
            self.log.error(f"_on_raw_frame: ch={channel_id} payload too short ({len(assembled)} bytes)")
            return

        message_id = struct.unpack_from(">H", assembled, 0)[0]
        body = assembled[2:]
        self._frames_received_count += 1

        self._recent_recv_processed_frames.append({
            "ts": ts,
            "ch": channel_id,
            "msg_id": f"0x{message_id:04x}",
            "len": len(body),
            "encrypted": encrypted,
        })

        ch_type_name = self.channel_type_map.get(channel_id)
        is_media_msg = message_id in (
            AVChannelMessage.Enum.AV_MEDIA_WITH_TIMESTAMP_INDICATION,
            AVChannelMessage.Enum.AV_MEDIA_INDICATION,
        )

        log_level = CHANNEL_MESSAGES_DEBUG_LEVELS.get(ch_type_name, {}).get(message_id, "info")
        if log_level != "None":
            log_method = getattr(self.log, log_level, self.log.info)
            log_method(f"📥 [TCP Recv] Inbound frame from phone: ch={channel_id}, msgId=0x{message_id:04x}, len={len(body)}, encrypted={encrypted}")

        if ch_type_name in ("VIDEO", "AUDIO") and is_media_msg:
            stream_type = 0 if ch_type_name == "VIDEO" else 1
            if message_id == AVChannelMessage.Enum.AV_MEDIA_WITH_TIMESTAMP_INDICATION:
                ts_us, media_payload = parse_media_with_timestamp(body)
            else:
                ts_us = 0
                media_payload = body

            if not media_payload:
                return

            if ch_type_name == "VIDEO":
                shm_offset = self._shm.transcode_in.write_frame(stream_type, ts_us, media_payload)
            else:
                ch_buf_size = 8 * 1024 * 1024
                shm_buf = self._shm.get_downstream_channel(channel_id, size=ch_buf_size)
                shm_offset = shm_buf.write_frame(channel_id, ts_us, media_payload)

            self.publish(
                "aa.frame.shm",
                {
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "encrypted": encrypted,
                    "shm_offset": shm_offset,
                    "timestamp_us": ts_us,
                    "payload_len": len(media_payload),
                },
            )
            # Zero-copy optimization: bypass redundant 100KB hex JSON allocations over ZMQ
            return

        body_hex = body.hex()
        frame_data = {
            "channel_id": channel_id,
            "message_id": message_id,
            "encrypted": encrypted,
            "payload_hex": body_hex,
        }
        received_data = {
            "channel_id": channel_id,
            "message_id": message_id,
            "encrypted": encrypted,
            "payload_hex": body_hex,
            "payload_head": body[:16].hex(),
        }

        self.publish("aa.frame.received", received_data)
        self.publish(f"aa.frame.ch{channel_id}", frame_data)


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
            ch_type_name = self.channel_type_map.get(channel_id)
            log_level = payload.get("log_level", None)
            log_level = log_level if log_level else CHANNEL_MESSAGES_DEBUG_LEVELS.get(ch_type_name, {}).get(message_id, "info")
            if log_level != "None":
                log_method = getattr(self.log, log_level, self.log.info)
                log_method(f"📤 [TCP Send] Transmitting frame to phone: ch={channel_id}, msgId=0x{message_id:04x}, len={len(body)}, encrypted={ssl_active}")

        except (KeyError, ValueError) as exc:
            self.log.error(f"on_frame_send: Malformed payload — {exc}")
            return

        ts = time.strftime("%H:%M:%S", time.localtime()) + f".{int(time.time()*1000)%1000:03d}"
        self._recent_sent_frames.append({
            "ts": ts,
            "ch": channel_id,
            "msg_id": f"0x{message_id:04x}",
            "len": len(body),
            "encrypted": ssl_active,
        })

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
        self.log.info(f"🤝 Handshake completed from {device_address} (phone_ip={phone_ip}) — initializing TCPServer listener...")
        self.start_tcp_server()

    def on_handshake_start_tls(self, topic: str, payload: dict) -> None:
        self.log.info("🔒 [TLS Stage 5/5] Initializing AACryptor (TLS 1.2 client role, Client Certificate loaded)...")
        with self._crypto_lock:
            self._cryptor = AACryptor()
            self._cryptor.init()
            outgoing = self._cryptor.drive_handshake()
        if outgoing:
            self.log.info(f"🔒 [TLS Stage 5/5] Generated TLS ClientHello ({len(outgoing)} bytes) — sending to phone on Channel 0...")
            self.on_frame_send("aa.frame.send", {
                "channel_id": 0,
                "message_id": ControlMessage.Enum.SSL_HANDSHAKE,
                "payload_hex": outgoing.hex(),
                "encrypted": False,
            })

    def on_handshake_feed_input(self, topic: str, payload: dict) -> None:
        if self._cryptor is None:
            return

        try:
            data = bytes.fromhex(payload["payload_hex"])
        except (KeyError, ValueError) as exc:
            self.log.error(f"on_handshake_feed_input: Malformed payload — {exc}")
            return

        self.log.info(f"🔒 [TLS Stage 5/5] Received TLS handshake input ({len(data)} bytes) from phone")
        with self._crypto_lock:
            self._cryptor.write_handshake_input(data)
            outgoing = self._cryptor.drive_handshake()
            if self._cryptor.is_active():
                self.log.info("🔒 [TLS Stage 5/5] 🎉 TLS HANDSHAKE COMPLETED SUCCESSFULLY! Cryptographic session active.")
            active = self._cryptor.is_active()

        if outgoing:
            self.log.info(f"🔒 [TLS Stage 5/5] Sending TLS handshake response ({len(outgoing)} bytes) to phone on Channel 0...")
            self.on_frame_send("aa.frame.send", {
                "channel_id": 0,
                "message_id": ControlMessage.Enum.SSL_HANDSHAKE,
                "payload_hex": outgoing.hex(),
                "encrypted": False,
            })

        if active:
            self.log.info("🔒 [TLS Stage 5/5] 🎉 TLS handshake complete — session encrypted!")
            self.publish("tcp.server.tls_handshake_completed", {})

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
            self.log.info("🔄 [TCP Server State] Session closed during pending restart — skipping teardown")
            return
        self.log.info("🌐 [TCP Server State] AA TCP session closed — tearing down socket & resetting cryptor/assembler state")
        self.publish("tcp.session.closed", {})
        self._teardown_server()
        if self._running:
            self.log.info("🔄 [TCP Server State] Auto-restarting TCP server listener loop for next connection...")
            self.start_tcp_server()

    def _teardown_server(self) -> None:
        self.log.info("🔄 [TCP Server State] Teardown initiated — cleaning up relay, server, cryptor, and assembler...")
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
        self.log.info("✅ [TCP Server State] Teardown complete — TCP server state reset cleanly")

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
