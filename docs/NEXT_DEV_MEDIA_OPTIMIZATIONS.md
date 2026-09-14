# Next Development: Media Pipeline Optimizations & Anti-Jitter Architecture

> **Status**: Approved for Next Development  
> **Source Inspiration**: [third_party/open-headunit](file:///home/nemo/NemoHeadUnit-Wireless/third_party/open-headunit) Analysis  
> **Targets**: Qt6 Native GUI (`qt6_gui`), WebCodecs Frontend (`frontend/js/webcodecs_player.js`), Backend `channel_manager` & `media_server`

---

## 1. Executive Summary

A comprehensive architectural analysis of `third_party/open-headunit` identified several low-level media and input optimization patterns designed to eliminate audio jitter, buffer underruns, video artifact stalls, and touch misalignment over wireless links.

This document details these patterns and maps out the concrete implementation tasks for the **Qt6 Native GUI**, **Web Frontend**, and **Backend Channel Manager**.

---

## 2. Audio Pipeline Optimizations & Anti-Jitter Engine

### 2.1 Unified Software Audio Mixer & Resampler
* **Source Reference**: [AudioMixer.kt](file:///home/nemo/NemoHeadUnit-Wireless/third_party/open-headunit/app/src/main/java/com/andrerinas/openheadunit/decoder/audio/AudioMixer.kt)
* **Rationale**: Android Auto negotiates multiple audio streams (`AUDIO` for media, `AUDIO1` for speech/Assistant, `AUDIO2` for navigation prompts). Relying on the OS or spinning up multiple discrete `QAudioSink` instances triggers audio routing glitches, clicks, and clock drift.
* **Target Architecture**:
  * Implement a dedicated software audio mixer running on a high-priority worker thread.
  * Standardize internal mixing on fixed **48,000 Hz, 16-bit signed PCM, Stereo**.
  * Use a fast-path integer-linear interpolation for 16kHz $\rightarrow$ 48kHz (Assistant/Nav prompts) with zero memory allocations:
    $$\text{interpolated} = \text{current} + \frac{(\text{next} - \text{current}) \times j}{3}$$

### 2.2 Pre-Rolling Anti-Underrun State Machine
* **Source Reference**: [AudioMixer.kt#L368-396](file:///home/nemo/NemoHeadUnit-Wireless/third_party/open-headunit/app/src/main/java/com/andrerinas/openheadunit/decoder/audio/AudioMixer.kt#L368-L396)
* **Rationale**: Over wireless Wi-Fi links, audio packets arrive in bursts. Playing audio immediately upon arrival of the first packet causes an immediate underrun/stutter on the next network jitter gap.
* **Mechanism**:
  * Each stream maintains a pre-roll state (`isPreRolling = True`).
  * On stream startup or after an underrun, accumulate **3 mix cycles (~60ms)** before releasing PCM samples to `QAudioSink` or Web Audio.
  * If a buffer drops below 1 cycle during playback, gracefully transition back to pre-rolling rather than outputting truncated or corrupted fractional frames.

### 2.3 Soft-Clipping Saturation Curve
* **Source Reference**: [AudioMixer.kt#L338-352](file:///home/nemo/NemoHeadUnit-Wireless/third_party/open-headunit/app/src/main/java/com/andrerinas/openheadunit/decoder/audio/AudioMixer.kt#L338-L352)
* **Rationale**: Summing media audio (gain 1.0) and navigation/system prompts (gain 1.0) causes digital wrap-around and harsh acoustic distortion.
* **Algorithm**: Compress any mixed sample exceeding $-4\text{ dB}$ ($> 20,480$ amplitude):
  $$\text{sample} = 20480 + \frac{\text{diff} \times 12287}{\text{diff} + 24574}$$

### 2.4 Lock-Free & Garbage-Free Circular Ring Buffers
* **Source Reference**: [AudioMixer.kt#L80-102](file:///home/nemo/NemoHeadUnit-Wireless/third_party/open-headunit/app/src/main/java/com/andrerinas/openheadunit/decoder/audio/AudioMixer.kt#L80-L102)
* **Rationale**: Allocating new byte arrays / buffers inside tight audio callback loops (20ms) causes GC spikes and scheduling pauses.
* **Mechanism**: Pre-allocate fixed-size circular ring buffers (`BidirectionalMediaSHM` or typed arrays) with zero allocations during active streaming.

---

## 3. Video Pipeline Resilience & Flow Control

### 3.1 Flow Control & `MediaAck` Uplink Prioritization
* **Source Reference**: [UplinkStallMonitor.kt](file:///home/nemo/NemoHeadUnit-Wireless/third_party/open-headunit/app/src/main/java/com/andrerinas/openheadunit/aap/UplinkStallMonitor.kt)
* **Rationale**: Android Auto enforces strict credit-based flow control (`max_unacked = 12` video frames, `30` audio frames). Delays in sending `MediaAck` responses cause the phone to halt video/audio transmissions simultaneously.
* **Mechanism**:
  * Set `TCP_NODELAY` on the AA TCP socket.
  * Emit `MediaAck` immediately upon packet consumption without waiting for video decoding/rendering to finish.

### 3.2 Transport Pacing vs. Non-Keyframe Drop Avoidance
* **Source Reference**: [VideoBackpressurePolicy.kt](file:///home/nemo/NemoHeadUnit-Wireless/third_party/open-headunit/app/src/main/java/com/andrerinas/openheadunit/decoder/video/VideoBackpressurePolicy.kt)
* **Rationale**: Dropping individual P-frames during transport congestion produces severe macroblocking / smear artifacts until the next I-frame.
* **Mechanism**:
  * Distinguish decoder backpressure from network burst drops.
  * When a frame drop is unavoidable, immediately dispatch an automated **Keyframe Request (Video Focus / Force SPS-PPS)** via `channel_manager` to force an immediate IDR frame from the phone.

---

## 4. Touch & Input Mapping Optimizations

### 4.1 Viewport Letterbox & Inset Compensation
* **Source Reference**: [TouchCoordinateMapper.kt](file:///home/nemo/NemoHeadUnit-Wireless/third_party/open-headunit/app/src/main/java/com/andrerinas/openheadunit/input/TouchCoordinateMapper.kt)
* **Rationale**: On ultrawide or non-16:9 displays, video is pillarboxed/letterboxed. Direct linear coordinate scaling results in touch offsets.
* **Algorithm**:
  * Calculate active video area:
    $$\text{uiRatio} = \frac{\text{negotiatedW}}{\text{negotiatedH}}, \quad \text{viewRatio} = \frac{\text{surfaceW}}{\text{surfaceH}}$$
  * Compute bounds offsets (`uiLeft`, `uiTop`) and map relative touch coordinates to the active AA projection surface.

### 4.2 Move / Drag Touch Throttling
* **Rationale**: High-frequency capacitive touch digitizers (120Hz–240Hz) flood the uplink TCP socket, causing `MediaAck` starvation.
* **Mechanism**: Throttle `ACTION_MOVE` / drag events to **30–33Hz (~30ms intervals)** while passing `ACTION_DOWN` and `ACTION_UP` immediately.

---

## 5. Auxiliary Channels to Implement (Full Parity with Open-Headunit)

To reach complete functional parity with `open-headunit`, implement the remaining auxiliary channels in `channel_manager`:

1. **Navigation Directions Channel (`ID_NAV = 10` / `NavigationChannelHandler`)**:
   * Parse `NextTurnDetail` (street names, maneuvers, next turn action) and `NextTurnDistanceEvent` (meters, time-to-turn).
   * Emit `navigation.turn_event` on ZMQ bus so Qt6/Web UI widgets can render turn-by-turn HUD overlays and notifications.
2. **Music Playback Metadata Channel (`ID_MPB = 9` / `MediaPlaybackChannelHandler`)**:
   * Parse `MediaPlaybackStatus` and `MediaMetaData` (Title, Artist, Album, large Album Art JPEG/PNG bitmap fragments).
   * Emit `media.metadata` on ZMQ bus to power bottom navbar track info and album art displays without needing third-party media players.
3. **Phone Call & Status Channel (`ID_PHONE = 12`)**:
   * Capture incoming call states and signal strength indicators.

---

## 6. Audio Sink Early Initialization & Anti-Discard Lifecycle

### The Problem in Current Qt6 Implementation:
In `audio_handler.py`, `_init_playback()` is called lazily on the first incoming audio frame. While `QAudioSink` is probing device formats, allocating audio buffers, and opening `sink_io` via `audio_sink.start()`, incoming audio frames pile up in `pcm_queue`. Because the queue has a strict cap (e.g. `MAX_QUEUE_LEN = 19200`), it immediately drops packets (`queue overflow! Dropped X bytes`), causing a harsh glitch or lost syllables at the beginning of playback.

### The Fix:
* **Eager Initialization**: Instantiate and open `QAudioSink` and start `playback_timer` **immediately upon session connection / module startup**, rather than on the first audio packet.
* **Session-Long Persistence**: Keep `QAudioSink` open in a warm playing state throughout the entire active Android Auto session, feeding silence / pre-roll buffers until data arrives.
* **Never Destroy Mid-Session**: Avoid tearing down and recreating the audio sink between consecutive tracks or audio pauses.

---

## 7. RAM Footprint & Embedded CPU Runtime Optimizations

On slow edge CPUs (e.g. Raspberry Pi, Intel Atom, Rockchip ARM SBCs), Python runtime overhead, GIL contention across threads, and memory footprint must be minimized:

1. **Cython / Nuitka Compilation**:
   * Compile performance-critical shared modules (`nal_utils.py`, `proto_utils.py`, `ipc_utils.py`, and `handlers/`) into native C extensions (`.so` / `.pyd`) using **Cython** or **Nuitka**. This removes CPython bytecode evaluation overhead and drastically reduces CPU usage.
2. **Alternative Runtimes (PyPy / Free-Threaded CPython 3.13+)**:
   * Benchmark PyPy JIT or CPython 3.13 free-threading (`--disable-gil`) for microservice backend processes.
3. **Zero Python Heap Allocations in Hot Paths**:
   * Replace Python `bytearray` resizing and slicing (`del self.pcm_queue[:dropped]`) with pre-allocated `ctypes` or `memoryview` circular ring buffers inside `media_shm.py`.
4. **Multiprocessing / Microservices over In-Process Threads**:
   * Nemo's design already uses process-isolated modules (`BaseBackendModule`) over ZeroMQ, bypassing the Python Global Interpreter Lock (GIL) and taking full advantage of multi-core hardware without thread locking bottlenecks.

---

## 8. Development Roadmap & Task Breakdown

```
Priority Wave 1 (P0): Audio Anti-Jitter & Early Sink Lifecycle
├── [ ] Eagerly instantiate and open `QAudioSink` on session start (prevent initial packet drops)
├── [ ] Implement Unified Software Audio Mixer in `qt6_gui/media/audio_handler.py`
├── [ ] Implement 60ms Pre-Roll Queue to eliminate initial audio stutter
└── [ ] Add Soft-Clipping saturation math to prevent digital distortion on audio overlap

Priority Wave 2 (P0): Video Pacing & Keyframe Auto-Recovery
├── [ ] Verify immediate `MediaAck` dispatch in `channel_manager/handlers.py`
└── [ ] Implement automated keyframe request trigger upon packet loss / decoder reset

Priority Wave 3 (P1): Missing Auxiliary Channels
├── [ ] Implement `NavigationChannelHandler` (`ID_NAV = 10`) for turn-by-turn notifications
└── [ ] Implement `MediaPlaybackChannelHandler` (`ID_MPB = 9`) for track metadata & album art

Priority Wave 4 (P2): Runtime & RAM Optimization
├── [ ] Port hot-path packet parsers (`nal_utils.py`, `proto_utils.py`) to Cython / C extensions
└── [ ] Eliminate dynamic allocations in audio/video streaming loops with `memoryview` ring buffers
```
