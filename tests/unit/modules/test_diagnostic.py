# tests/unit/modules/test_diagnostic.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from modules.diagnostic.main import DiagnosticModule

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_diagnostic():
    with patch("shared.base_module.BusClient"):
        mod = DiagnosticModule()
        yield mod


def test_diagnostic_config_and_schema(mock_diagnostic):
    cfg = mock_diagnostic.get_default_config()
    assert cfg["default_tone_freq"] == 440
    assert cfg["benchmark_fps"] == 30

    schema = mock_diagnostic.get_schema()
    assert "default_tone_freq" in schema
    assert "benchmark_fps" in schema


@pytest.mark.asyncio
async def test_diagnostic_status_and_capabilities(mock_diagnostic):
    # GET /status
    req = MagicMock()
    resp = await mock_diagnostic._handle_get_status(req)
    assert resp.status == 200
    import json
    data = json.loads(resp.text)
    assert data["status"] == "ok"
    assert data["active_test"] is None

    # GET /capabilities (calls media_server)
    mock_diagnostic.call_module = AsyncMock(return_value={"video_decoders": ["avdec_h264"]})
    resp_caps = await mock_diagnostic._handle_get_capabilities(req)
    assert resp_caps.status == 200
    caps_data = json.loads(resp_caps.text)
    assert "video_decoders" in caps_data

    # Fallback when media_server fails
    mock_diagnostic.call_module.side_effect = Exception("offline")
    resp_fail = await mock_diagnostic._handle_get_capabilities(req)
    assert resp_fail.status == 200
    fail_data = json.loads(resp_fail.text)
    assert fail_data["status"] == "partial"


@pytest.mark.asyncio
async def test_diagnostic_run_and_stop_lifecycle(mock_diagnostic):
    mock_diagnostic.call_module = AsyncMock(return_value={"status": "injected"})

    # POST /run (audio_pcm)
    req_run = MagicMock()
    req_run.can_read_body = True
    req_run.json = AsyncMock(return_value={"test_type": "audio_pcm", "params": {"tone_hz": 440, "duration_ms": 100}})
    resp_run = await mock_diagnostic._handle_post_run(req_run)
    assert resp_run.status == 200

    # Running while active returns 409
    resp_busy = await mock_diagnostic._handle_post_run(req_run)
    assert resp_busy.status == 409

    # POST /stop
    req_stop = MagicMock()
    resp_stop = await mock_diagnostic._handle_post_stop(req_stop)
    assert resp_stop.status == 200
    assert mock_diagnostic._active_test is None


@pytest.mark.asyncio
async def test_diagnostic_bus_events_and_broadcast(mock_diagnostic):
    mock_ws = AsyncMock()
    mock_diagnostic.ws_clients.add(mock_ws)

    # 1. _on_audio_frame
    await mock_diagnostic._on_audio_frame("media.audio.frame_shm", {
        "synthetic": True,
        "format": "pcm",
        "len": 1024,
    })
    mock_ws.send_str.assert_called()

    # 2. _on_mic_audio
    mock_ws.send_str.reset_mock()
    await mock_diagnostic._on_mic_audio("media.audio.mic_shm", {"len": 512})
    mock_ws.send_str.assert_called()

    # 3. _on_sink_changed
    mock_ws.send_str.reset_mock()
    await mock_diagnostic._on_sink_changed("media.audio.sink_changed", {"sink": "speakers", "source": "mic"})
    mock_ws.send_str.assert_called()

    # 4. _on_video_transport_active
    mock_ws.send_str.reset_mock()
    await mock_diagnostic._on_video_transport_active("media.video.transport_active", {"transport_name": "gstreamer"})
    mock_ws.send_str.assert_called()


@pytest.mark.asyncio
async def test_diagnostic_teardown(mock_diagnostic):
    mock_ws = AsyncMock()
    mock_diagnostic.ws_clients.add(mock_ws)
    mock_task = MagicMock()
    mock_task.done.return_value = False
    mock_diagnostic._test_task = mock_task

    await mock_diagnostic.teardown()
    mock_ws.close.assert_called_once()
    mock_task.cancel.assert_called_once()
    assert len(mock_diagnostic.ws_clients) == 0

