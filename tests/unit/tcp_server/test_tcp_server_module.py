import json
import struct
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from modules.tcp_server.main import TCPServerModule
from protos.oaa.control.ControlMessageIdsEnum_pb2 import ControlMessage
from protos.oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_tcp_server_mod():
    with patch("shared.base_module.BusClient"), \
         patch("modules.tcp_server.main.BidirectionalMediaSHM"):
        mod = TCPServerModule()
        yield mod


def test_tcp_server_module_config_and_schema(mock_tcp_server_mod):
    defaults = mock_tcp_server_mod.get_default_config()
    assert defaults["host"] == "0.0.0.0"
    assert defaults["port"] == 5288
    assert defaults["autostart"] is True

    schema = mock_tcp_server_mod.get_schema()
    assert "port" in schema
    assert schema["port"].min == 1024


@pytest.mark.asyncio
async def test_tcp_server_module_status_rest_api(mock_tcp_server_mod):
    req = MagicMock()
    resp = await mock_tcp_server_mod.handle_get_status(req)
    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["status"] == "ok"
    assert data["port"] == 5288
    assert data["server_running"] is False
    assert data["tls_active"] is False


def test_tcp_server_module_on_frame_send(mock_tcp_server_mod):
    mock_relay = MagicMock()
    mock_tcp_server_mod._relay = mock_relay

    mock_tcp_server_mod.on_frame_send("aa.frame.send", {
        "channel_id": 0,
        "message_id": ControlMessage.Enum.VERSION_REQUEST,
        "payload_hex": "0102",
        "encrypted": False,
    })

    mock_relay.send_raw.assert_called_once()
    assert mock_tcp_server_mod._frames_sent_count == 1


def test_tcp_server_module_on_raw_frame_control_dispatch(mock_tcp_server_mod):
    mock_tcp_server_mod.publish = MagicMock()
    mock_tcp_server_mod._assembler = MagicMock()
    # Assembler returns completed message: ch 0, flags 0x03, body: [msg_id: 2B][payload: 2B]
    assembled_bytes = bytes.fromhex("00010203")
    mock_tcp_server_mod._assembler.feed.return_value = (0, 0x03, assembled_bytes, len(assembled_bytes))

    mock_tcp_server_mod._on_raw_frame(channel_id=0, flags=0x03, payload=assembled_bytes, total_size=len(assembled_bytes))

    mock_tcp_server_mod.publish.assert_any_call("aa.frame.received", {
        "channel_id": 0,
        "message_id": 1,
        "encrypted": False,
        "payload_hex": "0203",
        "payload_head": "0203",
    })
    mock_tcp_server_mod.publish.assert_any_call("aa.frame.ch0", {
        "channel_id": 0,
        "message_id": 1,
        "encrypted": False,
        "payload_hex": "0203",
    })


def test_tcp_server_module_media_shm_routing(mock_tcp_server_mod):
    mock_tcp_server_mod.publish = MagicMock()
    mock_tcp_server_mod.channel_type_map[3] = "VIDEO"
    mock_tcp_server_mod._assembler = MagicMock()

    msg_id = AVChannelMessage.Enum.AV_MEDIA_INDICATION
    assembled_bytes = struct.pack(">H", msg_id) + b"video_frame_data"
    mock_tcp_server_mod._assembler.feed.return_value = (3, 0x03, assembled_bytes, len(assembled_bytes))

    mock_tcp_server_mod._shm.transcode_in.write_frame.return_value = 1234

    mock_tcp_server_mod._on_raw_frame(channel_id=3, flags=0x03, payload=assembled_bytes, total_size=len(assembled_bytes))

    mock_tcp_server_mod.publish.assert_called_once_with("aa.frame.shm", {
        "channel_id": 3,
        "message_id": msg_id,
        "encrypted": False,
        "shm_offset": 1234,
        "timestamp_us": 0,
        "payload_len": len(b"video_frame_data"),
    })


def test_tcp_server_module_media_with_timestamp_shm_routing(mock_tcp_server_mod):
    mock_tcp_server_mod.publish = MagicMock()
    mock_tcp_server_mod.channel_type_map[3] = "VIDEO"
    mock_tcp_server_mod._assembler = MagicMock()

    msg_id = AVChannelMessage.Enum.AV_MEDIA_WITH_TIMESTAMP_INDICATION
    timestamp_us = 987654321
    video_data = b"video_slice_payload"
    # parse_media_with_timestamp expects [8B BE timestamp][data]
    media_body = struct.pack(">Q", timestamp_us) + video_data
    assembled_bytes = struct.pack(">H", msg_id) + media_body
    mock_tcp_server_mod._assembler.feed.return_value = (3, 0x03, assembled_bytes, len(assembled_bytes))

    mock_tcp_server_mod._shm.transcode_in.write_frame.return_value = 5678

    mock_tcp_server_mod._on_raw_frame(channel_id=3, flags=0x03, payload=assembled_bytes, total_size=len(assembled_bytes))

    mock_tcp_server_mod.publish.assert_called_once_with("aa.frame.shm", {
        "channel_id": 3,
        "message_id": msg_id,
        "encrypted": False,
        "shm_offset": 5678,
        "timestamp_us": timestamp_us,
        "payload_len": len(video_data),
    })


def test_tcp_server_module_audio_media_shm_routing(mock_tcp_server_mod):
    mock_tcp_server_mod.publish = MagicMock()
    mock_tcp_server_mod.channel_type_map[4] = "AUDIO"
    mock_tcp_server_mod._assembler = MagicMock()

    msg_id = AVChannelMessage.Enum.AV_MEDIA_INDICATION
    audio_data = b"pcm_audio_sample_bytes"
    assembled_bytes = struct.pack(">H", msg_id) + audio_data
    mock_tcp_server_mod._assembler.feed.return_value = (4, 0x03, assembled_bytes, len(assembled_bytes))

    mock_audio_buf = MagicMock()
    mock_audio_buf.write_frame.return_value = 4321
    mock_tcp_server_mod._shm.get_downstream_channel.return_value = mock_audio_buf

    mock_tcp_server_mod._on_raw_frame(channel_id=4, flags=0x03, payload=assembled_bytes, total_size=len(assembled_bytes))

    mock_tcp_server_mod._shm.get_downstream_channel.assert_called_once_with(4, size=8 * 1024 * 1024)
    mock_audio_buf.write_frame.assert_called_once_with(4, 0, audio_data)
    mock_tcp_server_mod.publish.assert_called_once_with("aa.frame.shm", {
        "channel_id": 4,
        "message_id": msg_id,
        "encrypted": False,
        "shm_offset": 4321,
        "timestamp_us": 0,
        "payload_len": len(audio_data),
    })


def test_tcp_server_module_on_raw_frame_encrypted_decrypt(mock_tcp_server_mod):
    mock_tcp_server_mod.publish = MagicMock()
    mock_cryptor = MagicMock()
    mock_cryptor.is_active.return_value = True
    decrypted_body = bytes.fromhex("0001aabb")
    mock_cryptor.decrypt.return_value = decrypted_body
    mock_tcp_server_mod._cryptor = mock_cryptor

    mock_tcp_server_mod._assembler = MagicMock()
    mock_tcp_server_mod._assembler.feed.return_value = (0, 0x0B, decrypted_body, len(decrypted_body))

    raw_encrypted_bytes = b"encrypted_tls_ciphertext"
    # flags=0x0B includes 0x08 (_FLAG_ENCRYPTED)
    mock_tcp_server_mod._on_raw_frame(channel_id=0, flags=0x0B, payload=raw_encrypted_bytes, total_size=len(raw_encrypted_bytes))

    mock_cryptor.decrypt.assert_called_once_with(raw_encrypted_bytes)
    mock_tcp_server_mod._assembler.feed.assert_called_once_with(0, 0x0B, decrypted_body, len(raw_encrypted_bytes))
    mock_tcp_server_mod.publish.assert_any_call("aa.frame.ch0", {
        "channel_id": 0,
        "message_id": 1,
        "encrypted": True,
        "payload_hex": "aabb",
    })


@pytest.mark.asyncio
async def test_tcp_server_module_on_sdr_channels(mock_tcp_server_mod):
    await mock_tcp_server_mod.on_sdr_channels({
        "type_map": {
            "0": "CONTROL",
            "1": "INPUT",
            "3": "VIDEO",
            "5": "AUDIO_MEDIA",
        }
    })
    assert mock_tcp_server_mod.channel_type_map[0] == "CONTROL"
    assert mock_tcp_server_mod.channel_type_map[1] == "INPUT"
    assert mock_tcp_server_mod.channel_type_map[3] == "VIDEO"
    assert mock_tcp_server_mod.channel_type_map[5] == "AUDIO_MEDIA"


@pytest.mark.asyncio
async def test_tcp_server_module_restart_api(mock_tcp_server_mod):
    mock_tcp_server_mod.on_aa_session_restart = MagicMock()
    req = MagicMock()
    resp = await mock_tcp_server_mod.handle_post_restart(req)
    assert resp.status == 200
    mock_tcp_server_mod.on_aa_session_restart.assert_called_once_with("aa.session.restart", {})


@pytest.mark.asyncio
async def test_tcp_server_module_teardown(mock_tcp_server_mod):
    mock_relay = MagicMock()
    mock_server = MagicMock()
    mock_cryptor = MagicMock()
    mock_assembler = MagicMock()

    mock_tcp_server_mod._relay = mock_relay
    mock_tcp_server_mod._server = mock_server
    mock_tcp_server_mod._cryptor = mock_cryptor
    mock_tcp_server_mod._assembler = mock_assembler

    await mock_tcp_server_mod.teardown()

    mock_relay.stop.assert_called_once()
    mock_server.stop.assert_called_once()
    mock_cryptor.deinit.assert_called_once()
    mock_assembler.reset.assert_called_once()
    assert mock_tcp_server_mod._relay is None
    assert mock_tcp_server_mod._server is None
    assert mock_tcp_server_mod._cryptor is None
    assert mock_tcp_server_mod._assembler is None


def test_tcp_server_module_handshake_events(mock_tcp_server_mod):
    mock_tcp_server_mod.on_frame_send = MagicMock()
    mock_tcp_server_mod.publish = MagicMock()

    with patch("modules.tcp_server.main.AACryptor") as mock_cryptor_cls:
        mock_cryptor = MagicMock()
        mock_cryptor_cls.return_value = mock_cryptor
        mock_cryptor.drive_handshake.return_value = b"\x16\x03\x03client_hello"
        mock_cryptor.is_active.return_value = False

        # 1. Start TLS
        mock_tcp_server_mod.on_handshake_start_tls("aa.handshake.start_tls", {})
        mock_cryptor.init.assert_called_once()
        mock_tcp_server_mod.on_frame_send.assert_called_once_with("aa.frame.send", {
            "channel_id": 0,
            "message_id": ControlMessage.Enum.SSL_HANDSHAKE,
            "payload_hex": b"\x16\x03\x03client_hello".hex(),
            "encrypted": False,
        })

        # 2. Feed input completing handshake
        mock_cryptor.drive_handshake.return_value = b"\x16\x03\x03client_finished"
        mock_cryptor.is_active.return_value = True

        mock_tcp_server_mod.on_handshake_feed_input("aa.handshake.feed_input", {
            "payload_hex": "01020304"
        })
        mock_cryptor.write_handshake_input.assert_called_once_with(b"\x01\x02\x03\x04")
        mock_tcp_server_mod.publish.assert_called_with("tcp.server.tls_handshake_completed", {})


def test_tcp_server_module_start_when_already_active(mock_tcp_server_mod):
    with patch("threading.Thread") as mock_thread_cls:
        mock_tcp_server_mod._server = MagicMock()
        mock_tcp_server_mod.start_tcp_server()
        mock_thread_cls.assert_not_called()


def test_tcp_server_module_start_launches_thread(mock_tcp_server_mod):
    with patch("threading.Thread") as mock_thread_cls:
        mock_thread = MagicMock()
        mock_thread_cls.return_value = mock_thread
        mock_tcp_server_mod._server = None
        mock_tcp_server_mod._server_starting = False

        mock_tcp_server_mod.start_tcp_server()
        mock_thread_cls.assert_called_once()
        mock_thread.start.assert_called_once()


def test_tcp_server_module_on_session_closed(mock_tcp_server_mod):
    mock_tcp_server_mod.publish = MagicMock()
    mock_tcp_server_mod._teardown_server = MagicMock()
    mock_tcp_server_mod.start_tcp_server = MagicMock()
    mock_tcp_server_mod._running = True
    mock_tcp_server_mod._restart_pending = False

    mock_tcp_server_mod._on_session_closed()

    mock_tcp_server_mod.publish.assert_called_once_with("tcp.session.closed", {})
    mock_tcp_server_mod._teardown_server.assert_called_once()
    mock_tcp_server_mod.start_tcp_server.assert_called_once()


def test_tcp_server_module_on_session_closed_during_restart_pending(mock_tcp_server_mod):
    mock_tcp_server_mod.publish = MagicMock()
    mock_tcp_server_mod._teardown_server = MagicMock()
    mock_tcp_server_mod._restart_pending = True

    mock_tcp_server_mod._on_session_closed()

    mock_tcp_server_mod.publish.assert_not_called()
    mock_tcp_server_mod._teardown_server.assert_not_called()


def test_tcp_server_module_on_ch0_frame_shutdown_ack(mock_tcp_server_mod):
    from modules.tcp_server.main import _MSG_SHUTDOWN_RESPONSE
    mock_tcp_server_mod._restart_pending = True
    mock_tcp_server_mod._shutdown_ack_event.clear()

    mock_tcp_server_mod.on_ch0_frame("aa.frame.ch0", {"message_id": _MSG_SHUTDOWN_RESPONSE})
    assert mock_tcp_server_mod._shutdown_ack_event.is_set()


def test_tcp_server_module_on_frame_send_malformed(mock_tcp_server_mod):
    mock_relay = MagicMock()
    mock_tcp_server_mod._relay = mock_relay

    # Missing message_id / invalid payload
    mock_tcp_server_mod.on_frame_send("aa.frame.send", {"channel_id": "bad"})
    mock_relay.send_raw.assert_not_called()


def test_tcp_server_module_on_raw_frame_too_short(mock_tcp_server_mod):
    mock_tcp_server_mod._assembler = MagicMock()
    # Payload less than 2 bytes
    mock_tcp_server_mod._assembler.feed.return_value = (0, 0x03, b"1", 1)
    mock_tcp_server_mod.publish = MagicMock()

    mock_tcp_server_mod._on_raw_frame(0, 0x03, b"1", 1)
    # Should not publish since payload is too short for 2B message ID
    mock_tcp_server_mod.publish.assert_not_called()


def test_tcp_server_module_on_raw_frame_decrypt_failure_logs_error(mock_tcp_server_mod):
    mock_cryptor = MagicMock()
    mock_cryptor.is_active.return_value = True
    mock_cryptor.decrypt.side_effect = RuntimeError("decryption failed")
    mock_tcp_server_mod._cryptor = mock_cryptor
    mock_tcp_server_mod._assembler = MagicMock()
    # flags with 0x08 (_FLAG_ENCRYPTED)
    mock_tcp_server_mod._on_raw_frame(channel_id=0, flags=0x0B, payload=b"bad_encrypted_payload", total_size=20)
    mock_tcp_server_mod._assembler.feed.assert_not_called()


@pytest.mark.asyncio
async def test_tcp_server_module_setup_and_restart(mock_tcp_server_mod):
    await mock_tcp_server_mod.setup()

    # Test POST restart
    req = MagicMock()
    with patch.object(mock_tcp_server_mod, "on_aa_session_restart") as m_restart:
        resp = await mock_tcp_server_mod.handle_post_restart(req)
        assert resp.status == 200
        m_restart.assert_called_once()

    # Test teardown
    with patch.object(mock_tcp_server_mod, "_teardown_server") as m_td:
        await mock_tcp_server_mod.teardown()
        m_td.assert_called_once()


def test_tcp_server_module_on_aa_session_restart(mock_tcp_server_mod):
    # Case 1: _relay is None
    mock_tcp_server_mod._relay = None
    mock_tcp_server_mod.on_aa_session_restart("aa.session.restart", {})

    # Case 2: _relay present
    mock_relay = MagicMock()
    mock_cryptor = MagicMock()
    mock_cryptor.is_active.return_value = False
    mock_assembler = MagicMock()

    mock_tcp_server_mod._relay = mock_relay
    mock_tcp_server_mod._cryptor = mock_cryptor
    mock_tcp_server_mod._assembler = mock_assembler
    mock_tcp_server_mod.publish = MagicMock()

    # Pre-set shutdown ack so test doesn't wait
    mock_tcp_server_mod._shutdown_ack_event.set()

    mock_tcp_server_mod.on_aa_session_restart("aa.session.restart", {})
    mock_relay.send_raw.assert_called()
    mock_cryptor.deinit.assert_called_once()
    mock_assembler.reset.assert_called_once()
    mock_tcp_server_mod.publish.assert_called_with("aa.session.restarting", {})


def test_tcp_server_module_start_when_active(mock_tcp_server_mod):
    mock_tcp_server_mod._server = MagicMock()
    with patch("threading.Thread") as m_thread:
        mock_tcp_server_mod.start_tcp_server()
        m_thread.assert_not_called()


def test_tcp_server_module_on_sdr_channels_sync(mock_tcp_server_mod):
    import asyncio
    asyncio.run(mock_tcp_server_mod.on_sdr_channels({
        "type_map": {"1": "AV_CHANNEL_TYPE_VIDEO", "3": "AV_CHANNEL_TYPE_AUDIO"}
    }))
    assert mock_tcp_server_mod.channel_type_map[1] == "AV_CHANNEL_TYPE_VIDEO"
    assert mock_tcp_server_mod.channel_type_map[3] == "AV_CHANNEL_TYPE_AUDIO"


def test_tcp_server_module_handshake_completed_and_feed_errors(mock_tcp_server_mod):
    with patch.object(mock_tcp_server_mod, "start_tcp_server") as m_start:
        mock_tcp_server_mod.on_handshake_completed("aa.handshake.completed", {"device_address": "AA:BB", "phone_ip": "192.168.1.5"})
        m_start.assert_called_once()

    # Feed input with cryptor None
    mock_tcp_server_mod._cryptor = None
    mock_tcp_server_mod.on_handshake_feed_input("aa.handshake.feed_input", {"payload_hex": "1234"})

    # Feed input with malformed hex
    mock_tcp_server_mod._cryptor = MagicMock()
    mock_tcp_server_mod.on_handshake_feed_input("aa.handshake.feed_input", {"payload_hex": "not_hex"})


def test_tcp_server_module_on_frame_send_exceptions(mock_tcp_server_mod):
    mock_tcp_server_mod._relay = MagicMock()
    # Test encode exception
    with patch("modules.tcp_server.main.encode", side_effect=Exception("Encode failed")):
        mock_tcp_server_mod.on_frame_send("aa.frame.send", {
            "channel_id": 0,
            "message_id": 1,
            "payload_hex": "00",
        })

    # Test send_raw exception
    mock_tcp_server_mod._relay.send_raw.side_effect = Exception("Write failed")
    mock_tcp_server_mod.on_frame_send("aa.frame.send", {
        "channel_id": 0,
        "message_id": 1,
        "payload_hex": "00",
    })



