# Diagnostic Module Design Specification

**Author:** NemoHeadUnit-Wireless Team  
**Date:** 2026-09-01  
**Status:** In Review  
**Topic:** Audio & Video Diagnostic Subsystem (`modules/diagnostic`, `modules/media_server`, `qt6_gui`, Web Frontend)

---

## 1. Overview & Goals

The **Diagnostic Module** provides an interactive diagnostic and verification harness for the multimedia pipelines of NemoHeadUnit-Wireless. It allows developers and end-users to test every stage of audio and video transport in isolation and end-to-end, validating hardware acceleration, codec health, device routing, and latency without requiring an active Android Auto phone connection.

### Core Objectives:
1. **Point-by-Point Audio Testing:**
   - Level 1: Direct PCM tone synthesis → `QAudioSink` / device sink (validating buffer clocking and DAC).
   - Level 2: AAC packet generation → AAC decoder → `QAudioSink` (validating AAC stream parser and decoder).
   - Level 3: Audio Source (Mic) → Frame capture (validating microphone stream, RMS levels, clipping).
   - Level 4: Full End-to-End Loopback (Source → AAC Encode → AAC Decode → Sink).
   - Dynamic device selection (temporary switch of active sink/source).
2. **Video & HW Acceleration Testing:**
   - Forced HWaccel probe (`v4l2slh264dec`, `vaapih264dec`, `nvdec`, `d3d11vah264dec`, `avdec_h264`).
   - Transport re-encode benchmark (`mjpeg`, `webp`, `yuv420`, `rgba`, `h264`) measuring throughput FPS, latency, and frame drops using synthetic SMPTE color-bar H.264 streams.
   - Real-time rendering check on Qt6 GUI Viewport and Web frontend.
3. **Architecture & Environment Compliance:**
   - Reuses internal methods of `media_server` to validate real production code paths.
   - Fully compatible with both **Multiprocess** and **Multithread** execution modes.
   - Cross-platform support for **Linux** (PipeWire/PulseAudio/V4L2/VAAPI) and **Windows** (WASAPI/D3D11/NVDEC).

---

## 2. Architecture & Data Flow

```
                      ┌──────────────────────────────────────────────┐
                      │    Diagnostic Module (/api/diagnostic)       │
                      │  - Test Suite Orchestrator                   │
                      │  - Metric Collector & WS Broadcast           │
                      └──────────────┬───────────────────────────────┘
                                     │ RPC: self.call_module("media_server", ...)
                                     ▼
                      ┌──────────────────────────────────────────────┐
                      │      Media Server (/api/media/diagnostic)    │
                      │  - Video Transports (HW/SW decode & encode)  │
                      │  - Audio Adapter & SHM Frame Dispatcher      │
                      └──────┬──────────────────────────────┬────────┘
                             │                              │
          SHM / ZMQ Audio    │           SHM Transport / WS │ Decoded Video
                             ▼                              ▼
                 ┌───────────────────────┐      ┌────────────────────────┐
                 │  Qt6 GUI / Web Audio  │      │  Qt6 GUI Video / Web   │
                 │  - QAudioSink (Output)│      │  - Video Viewport (GL) │
                 │  - QAudioSource (Mic) │      │  - Web Canvas Stream   │
                 └───────────────────────┘      └────────────────────────┘
```

---

## 3. Subsystem Detailed Design

### 3.1 `media_server` Diagnostic Endpoints (`/api/media/diagnostic/*`)
`media_server` exposes lightweight diagnostic endpoints reusing its internal pipeline engines:

1. **`GET /api/media/diagnostic/capabilities`**
   - Returns host OS capabilities: detected audio output sinks, input sources, supported GStreamer / FFmpeg video decoders and encoders (V4L2, VAAPI, D3D11VA, NVDEC, software).
2. **`POST /api/media/diagnostic/audio/inject`**
   - Payload: `{"format": "pcm"|"aac", "tone_hz": 440, "duration_ms": 2000, "channels": 2, "sample_rate": 48000}`
   - Behavior:
     - For `pcm`: Synthesizes pure sine wave / chime in memory and writes directly to audio ring buffer (`shm.write_audio_frame()` or `media.audio.frame_shm`).
     - For `aac`: Synthesizes or retrieves bundled reference AAC stream and passes through AAC decode/demux engine before writing to audio sink.
3. **`POST /api/media/diagnostic/audio/set_device`**
   - Payload: `{"sink_name": "...", "source_name": "..."}`
   - Dynamic switch of active audio device via `audio_adapter` and notification broadcast `media.audio.sink_changed`.
4. **`POST /api/media/diagnostic/video/benchmark`**
   - Payload: `{"transport": "mjpeg"|"webp"|"yuv420"|"rgba"|"h264", "decoder": "auto"|"forced_v4l2"|"forced_vaapi"|"forced_d3d11va"|"forced_nvdec"|"sw", "duration_sec": 3, "fps": 30}`
   - Behavior:
     - Instantiates target transport strategy with requested decoder override.
     - Feeds synthetic SMPTE color-bar H.264 NAL sequence at target FPS.
     - Measures: actual decoded FPS, end-to-end latency per frame, dropped frames, decoder plugin name used.
     - Returns summary JSON metrics.

---

### 3.2 `modules/diagnostic` Module (`/api/diagnostic/*`)
Inherits from `BaseBackendModule` (`priority=5`, path prefix `/api/diagnostic`).

- **Lifecycle:**
  - `setup()`: Discovers `media_server` via `call_module` / heartbeat registry. Subscribes to audio capture / metric topics.
  - `run()`: Serves REST & WebSocket routes.
  - `teardown()`: Aborts any in-progress tests and restores default audio/video routing.
- **REST Endpoints:**
  - `GET /api/diagnostic/status`: Current running test status.
  - `POST /api/diagnostic/run`: Initiates an automated test suite or specific test step (`audio_pcm`, `audio_aac`, `audio_mic`, `audio_loopback`, `video_hwaccel`, `video_matrix`).
  - `POST /api/diagnostic/stop`: Stops running diagnostic stream.
- **WebSocket Endpoint (`/api/diagnostic/ws`):**
  - Broadcasts live events: `{ "type": "audio_level", "rms_db": -12.4, "peak_db": -3.1 }`, `{ "type": "video_frame", "fps": 29.8, "latency_ms": 14.2, "decoder": "v4l2slh264dec" }`, `{ "type": "test_completed", "results": {...} }`.

---

### 3.3 Audio Test Matrix

| Test ID | Path Under Test | Payload | Validation Criteria |
|---------|-----------------|---------|---------------------|
| `AUD-01` | PCM Direct → Sink | 16-bit 48kHz Stereo Sine (440Hz / 1kHz) | `QAudioSink` consumes samples without underrun or drop |
| `AUD-02` | AAC → Decoder → Sink | Bundled test AAC packet stream | AAC parser decodes to PCM, audible chime on sink |
| `AUD-03` | Mic Capture → Frame | Live capture from `QAudioSource` / ALSA / Pulse | RMS and Peak dB levels stream in real-time, clipping detected |
| `AUD-04` | Mic → Loopback → Sink | Mic buffer piped to output sink with delay buffer | Audio loops back with measurable latency |
| `AUD-05` | Device Switch | Change target audio sink/source dynamically | Audio moves to new device cleanly without process crash |

---

### 3.4 Video Test Matrix

| Test ID | Path Under Test | Parameters | Validation Criteria |
|---------|-----------------|------------|---------------------|
| `VID-01` | Forced HW Decoder Probe | `v4l2slh264dec` / `vaapih264dec` / `d3d11va` / `nvdec` | HW decoder initializes cleanly without fallback error |
| `VID-02` | SW Fallback Decoder Probe | `avdec_h264` | Software decoder operates properly across all platforms |
| `VID-03` | Transport Re-encode: MJPEG | H.264 → Decode → JPEG Encode | ≥ 30 FPS, JPEG header valid, rendered on viewport |
| `VID-04` | Transport Re-encode: WebP | H.264 → Decode → WebP Encode | ≥ 30 FPS, WebP frame delivered to Web frontend |
| `VID-05` | Transport Re-encode: YUV420 / RGBA | H.264 → Decode → Raw buffer | Raw frame delivered to SHM / WebGL canvas |

---

### 3.5 Cross-Platform & Execution Modes

1. **Multiprocess vs Multithread Mode:**
   - All inter-module calls use `self.call_module("media_server", ...)` which resolves dynamically to loopback HTTP in multiprocess mode or in-process router in multithread mode.
   - ZMQ topics use `get_bus_address()` from `shared/ipc_utils.py` (TCP on Windows, IPC/TCP on Linux).
2. **Linux & Windows Hardware Adaptation:**
   - Audio devices enumerated via `base_audio.py` (`linux_audio.py` / `windows_audio.py`) and `QMediaDevices` in GUI.
   - Video HWaccel probe checks for Windows DirectX / D3D11 / NVDEC decoders when `sys.platform == "win32"`, and V4L2 / VAAPI on Linux.

---

### 3.6 User Interface Integrations

#### Qt6 GUI (`backend/modules/qt6_gui/ui/drawers/diagnostics_drawer.py`)
- Slide-out drawer accessible from Command Bar and Settings Drawer.
- **Audio Diagnostic Section:**
  - Test tone buttons: "Test 440Hz Sine (PCM)", "Test Chime (AAC)", "Test Mic Capture".
  - Live VU meter bar (Green/Yellow/Red) showing mic input level.
  - Dropdowns for temporary Sink and Source selection.
- **Video Diagnostic Section:**
  - Decoder selector dropdown (Auto / Forced V4L2 / Forced VAAPI / Forced D3D11 / Forced NVDEC / Software).
  - Transport mode selector (MJPEG / WebP / YUV420 / RGBA / H264).
  - "Run Video Benchmark (3s)" button with real-time FPS, latency, and HW decoder badge display.
  - Test pattern preview on Viewport.

#### Web Frontend (`frontend/js/diagnostic.js` & `frontend/index.html`)
- Diagnostic modal / view tab with corresponding audio/video test triggers, live WebSocket graphs, and device status tables.

---

## 4. Verification Plan

1. **Smoke Test & Module Boot:**
   - Launch backend (`python web-browser-head-unit/backend/main.py`) and verify Wave 5 boots `diagnostic` module cleanly with zero warnings or port conflicts.
2. **Automated Unit / API Tests:**
   - Execute pytest covering `/api/diagnostic/status`, `/api/media/diagnostic/capabilities`, and synthetic test frame generation.
3. **Interactive Manual Test (Linux & Windows / Browser):**
   - Open Qt6 GUI and Web frontend.
   - Trigger `AUD-01` (PCM sine) → verify audible tone and zero underruns.
   - Trigger `AUD-03` (Mic test) → speak into mic and observe live VU meter movement.
   - Trigger `VID-01` & `VID-03` → verify SMPTE color bars display on screen at ≥30 FPS with HW decoder reported.
