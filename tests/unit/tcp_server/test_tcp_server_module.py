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
