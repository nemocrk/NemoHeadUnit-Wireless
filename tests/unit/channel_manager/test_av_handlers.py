import pytest
from unittest.mock import AsyncMock, MagicMock
from protos.oaa.av.AVChannelMessageIdsEnum_pb2 import AVChannelMessage
from protos.oaa.av.AVChannelSetupRequestMessage_pb2 import AVChannelSetupRequest
from protos.oaa.av.AVChannelSetupResponseMessage_pb2 import AVChannelSetupResponse
from protos.oaa.av.AVChannelSetupStatusEnum_pb2 import AVChannelSetupStatus
from protos.oaa.av.AVChannelStartIndicationMessage_pb2 import AVChannelStartIndication
from protos.oaa.av.AVMediaAckIndicationMessage_pb2 import AVMediaAckIndication
from protos.oaa.video.VideoFocusIndicationMessage_pb2 import VideoFocusIndication
from protos.oaa.video.VideoFocusModeEnum_pb2 import VideoFocusMode
from shared.constants import ChannelType
from modules.channel_manager.handlers.video_handler import VideoChannelHandler, UNACKED_FRAMES_THRESHOLD
from modules.channel_manager.handlers.audio_handler import AudioChannelHandler

pytestmark = pytest.mark.unit
AV_MSG = AVChannelMessage.Enum


@pytest.fixture
def mock_av_manager():
    mgr = MagicMock()
    mgr.send_wire_frame = AsyncMock()
    mgr.publish = MagicMock()
    mgr.broadcast_ws_json = AsyncMock()
    mgr.broadcast_ws_media = AsyncMock()
    mgr.get_stream_config_dict.return_value = {"streams": {}}
    mgr.get_channel_id_for_type.side_effect = lambda t: 3 if t == ChannelType.VIDEO else 4
    mgr.active_channels = {3: {}, 4: {}}
    mgr.ws_clients = set()
    mgr.active_video_transport = "h264"
    return mgr


@pytest.mark.asyncio
async def test_video_handler_setup_and_start(mock_av_manager):
    video = VideoChannelHandler(mock_av_manager)
    setup_req = AVChannelSetupRequest(media_codec_type=3)  # H264

    # 1. SETUP_REQUEST -> SETUP_RESPONSE(OK, max_unacked=10)
    await video.handle_frame(channel_id=3, message_id=AV_MSG.SETUP_REQUEST, body=setup_req.SerializeToString())
    assert video.setup_completed is True
    # Check setup response and focus indication were sent
    assert mock_av_manager.send_wire_frame.call_count == 2
    setup_payload = mock_av_manager.send_wire_frame.call_args_list[0][0][2]
    mock_av_manager.send_wire_frame.assert_any_call(
        3, AV_MSG.SETUP_RESPONSE, setup_payload, encrypted=True
    )
    setup_resp = AVChannelSetupResponse()
    setup_resp.ParseFromString(setup_payload)
    assert setup_resp.media_status == AVChannelSetupStatus.Enum.OK
    assert setup_resp.max_unacked == 10
    assert 0 in setup_resp.configs

    # Verify focus indication was sent
    assert mock_av_manager.send_wire_frame.call_args_list[1][0][1] == AV_MSG.VIDEO_FOCUS_INDICATION
    focus_ind = VideoFocusIndication()
    focus_ind.ParseFromString(mock_av_manager.send_wire_frame.call_args_list[1][0][2])
    assert focus_ind.focus_mode == VideoFocusMode.Enum.PROJECTED

    # 2. START_INDICATION -> session_id extracted, published video.stream_start
    start_req = AVChannelStartIndication(session=42, config=0)
    await video.handle_frame(channel_id=3, message_id=AV_MSG.START_INDICATION, body=start_req.SerializeToString())
    assert video.session_id == 42
    mock_av_manager.publish.assert_called_with("video.stream_start", {
        "session_id": 42,
        "codec": "MEDIA_CODEC_VIDEO_H264_BP",
        "codec_enum": 3,
    })


@pytest.mark.asyncio
async def test_video_handler_shm_batch_acking(mock_av_manager):
    video = VideoChannelHandler(mock_av_manager)
    video.session_id = 100

    # Feed 9 frames: publishes raw NAL, no ACK yet
    for i in range(9):
        await video.process_shm_frame(message_id=0, offset=i * 100, ts_us=i * 1000, payload_len=100)
    assert mock_av_manager.send_wire_frame.call_count == 0
    assert video.unacked_frames == 9

    # 10th frame triggers batch ACK
    await video.process_shm_frame(message_id=0, offset=900, ts_us=9000, payload_len=100)
    assert mock_av_manager.send_wire_frame.call_count == 1
    ch, msg_id, payload = mock_av_manager.send_wire_frame.call_args[0][:3]
    assert ch == 3
    assert msg_id == AV_MSG.AV_MEDIA_ACK_INDICATION
    ack = AVMediaAckIndication()
    ack.ParseFromString(payload)
    assert ack.session_id == 100
    assert ack.ack_count == 10
    assert video.unacked_frames == 0


@pytest.mark.asyncio
async def test_video_handler_send_focus_indication(mock_av_manager):
    video = VideoChannelHandler(mock_av_manager)
    video.unacked_frames = 5
    video.session_id = 200

    # Non-PROJECTED focus indication flushes unacked frames first
    await video.send_focus_indication(VideoFocusMode.Enum.NATIVE)
    assert mock_av_manager.send_wire_frame.call_count == 2
    # First call: flushed unacked ACK
    assert mock_av_manager.send_wire_frame.call_args_list[0][0][1] == AV_MSG.AV_MEDIA_ACK_INDICATION
    ack = AVMediaAckIndication()
    ack.ParseFromString(mock_av_manager.send_wire_frame.call_args_list[0][0][2])
    assert ack.session_id == 200
    assert ack.ack_count == 5
    # Second call: focus indication
    assert mock_av_manager.send_wire_frame.call_args_list[1][0][1] == AV_MSG.VIDEO_FOCUS_INDICATION
    focus_ind = VideoFocusIndication()
    focus_ind.ParseFromString(mock_av_manager.send_wire_frame.call_args_list[1][0][2])
    assert focus_ind.focus_mode == VideoFocusMode.Enum.NATIVE
    assert video.unacked_frames == 0


@pytest.mark.asyncio
async def test_audio_handler_setup_and_start(mock_av_manager):
    audio = AudioChannelHandler(mock_av_manager)
    setup_req = AVChannelSetupRequest(media_codec_type=1)  # PCM

    # 1. SETUP_REQUEST -> SETUP_RESPONSE(OK, max_unacked=10)
    await audio.handle_frame(channel_id=4, message_id=AV_MSG.SETUP_REQUEST, body=setup_req.SerializeToString())
    mock_av_manager.send_wire_frame.assert_called_once()
    assert mock_av_manager.send_wire_frame.call_args[0][1] == AV_MSG.SETUP_RESPONSE
    setup_payload = mock_av_manager.send_wire_frame.call_args[0][2]
    setup_resp = AVChannelSetupResponse()
    setup_resp.ParseFromString(setup_payload)
    assert setup_resp.media_status == AVChannelSetupStatus.Enum.OK
    assert setup_resp.max_unacked == 10
    assert 0 in setup_resp.configs

    mock_av_manager.publish.assert_called_with("media.audio.channel_configured", {
        "channel_id": 4,
        "codec": "MEDIA_CODEC_AUDIO_PCM",
        "codec_enum": 1,
        "sample_rate": 48000,
        "channel_count": 2,
        "bit_depth": 16,
        "audio_type": "MEDIA",
    })

    # 2. START_INDICATION -> session_recorded, stream ACTIVE published
    mock_av_manager.send_wire_frame.reset_mock()
    start_req = AVChannelStartIndication(session=7, config=0)
    await audio.handle_frame(channel_id=4, message_id=AV_MSG.START_INDICATION, body=start_req.SerializeToString())
    assert audio.sessions[4] == 7
    mock_av_manager.publish.assert_called_with("media.audio.stream_status", {
        "channel_id": 4,
        "status": "ACTIVE",
        "session_id": 7,
    })


@pytest.mark.asyncio
async def test_audio_handler_shm_batch_acking(mock_av_manager):
    audio = AudioChannelHandler(mock_av_manager)
    audio.sessions[4] = 99

    for i in range(10):
        await audio.process_shm_frame(channel_id=4, message_id=0, offset=i * 50, ts_us=i * 500, payload_len=50)

    assert mock_av_manager.send_wire_frame.call_count == 1
    assert mock_av_manager.send_wire_frame.call_args[0][1] == AV_MSG.AV_MEDIA_ACK_INDICATION
    ack = AVMediaAckIndication()
    ack.ParseFromString(mock_av_manager.send_wire_frame.call_args[0][2])
    assert ack.session_id == 99
    assert ack.ack_count == 10
    assert audio.unacked_counts[4] == 0
