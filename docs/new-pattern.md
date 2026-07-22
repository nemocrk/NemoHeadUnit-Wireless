# Project Context: Ultra-Low-Latency Android Auto Emulator

## System Constraints & Target Hardware
You are developing a local video and audio streaming architecture for an Android Auto (AA) head unit emulator (`NemoHeadUnit-Wireless`). 
The target hardware is severely resource-constrained:
- **OS:** Lubuntu 26 (Linux)
- **CPU/GPU:** Intel Atom Z3770 (Bay Trail) with Gen7 Intel HD Graphics (`i965` VA-API driver).
- **RAM:** 2GB total system memory.
- **Goal:** Sub-50ms latency video streaming from a multiplexed UDP connection to a local browser, bypassing all unnecessary CPU overhead (zero re-encoding).

## The Architecture Pattern: "Dumb Pipe" Passthrough to WebCodecs

Because of the 2GB RAM and weak CPU, we cannot use heavy media frameworks (like FFmpeg/GStreamer) or browser-based MSE (Media Source Extensions). We must route the raw Android Auto H.264 NAL units directly to the browser's hardware GPU decoder using the **WebCodecs API**.

### System Flow
1. **Android Auto Source:** Sends multiplexed, encrypted H.264/Audio over a single UDP port.
2. **Python Backend (Demuxer):** Receives UDP, defragments large frames, decrypts the payload, extracts the AA timestamp, and sends a binary frame over a local WebSocket.
3. **JavaScript Frontend (Renderer):** Receives the binary WebSocket frame, identifies Keyframes, feeds the raw bytes into a `VideoDecoder`, and paints directly to an HTML5 `<canvas>`.
4. **Kiosk Browser:** WPE WebKit (`cog` launcher) or aggressively stripped Chromium `--app` mode with hardware acceleration forced on.

---

## Agent Instructions: Backend Implementation (Python)

When writing the Python backend, you MUST adhere to these rules:

1. **Use `asyncio.DatagramProtocol`:** Do not use blocking sockets. The UDP receiver must be entirely non-blocking.
2. **Defragmentation is Mandatory:** UDP MTU limits mean H.264 Keyframes (IDR) are split across multiple UDP packets. You must implement a buffer that reads the AA header's fragment flags (First, Middle, Last) and concatenates the payload into a single, complete NAL unit before sending it to the WebSocket. WebCodecs *cannot* decode fragmented chunks.
3. **Preserve Native Timestamps:** Extract the timestamp provided by the Android Auto protocol header. **Do not** generate a local timestamp via `time.time()`. Pass the native AA timestamp directly to the frontend to ensure perfect A/V lip-sync.
4. **Binary Protocol over WebSockets:** Pack the data using Python's `struct` module. JSON is strictly forbidden for media payloads due to serialization overhead.
   - **Schema:** `[StreamType: 1 Byte (0=Video, 1=Audio)] + [Timestamp: 8 Bytes (unsigned long long)] + [Decrypted Payload: N Bytes]`

---

## Agent Instructions: Frontend Implementation (JavaScript)

When writing the JavaScript frontend, you MUST adhere to these rules:

1. **Strictly WebCodecs:** Use `VideoDecoder` and `AudioDecoder`. Do not use `<video src="...">`, HLS, or MSE.
2. **Binary Parsing:** Set the WebSocket to `binaryType = "arraybuffer"`. Use `DataView` to read the custom binary protocol sent by Python.
3. **Frame Type Identification:** You must inspect the first byte of the H.264 NAL unit payload to determine the `EncodedVideoChunk` type.
   - Formula: `const nalUnitType = payload[0] & 0x1F;`
   - If `nalUnitType === 5`, set `type: "key"`. Otherwise, set `type: "delta"`.
4. **Timestamps in Microseconds:** WebCodecs requires the `timestamp` field of the `EncodedVideoChunk` to be in microseconds. Ensure the AA timestamp is converted appropriately.
5. **SPS/PPS Initialization (Crucial):** The stream will start with SPS (NAL type 7) and PPS (NAL type 8) frames. You must capture these and feed them into the `description` field of `VideoDecoder.configure()` or pass them in the correct Annex B format, otherwise, the decoder will immediately throw an error and permanently close.
6. **Canvas Rendering:** When the `VideoDecoder` outputs a `VideoFrame`, immediately render it to a 2D or WebGL canvas and call `frame.close()` to prevent memory leaks.

---

## Agent Instructions: Environment & Launch Scripts

When writing bash scripts for deployment, you MUST include:
1. Environmental variables forcing the legacy Intel VA-API driver (`export LIBVA_DRIVER_NAME=i965`).
2. Commands to launch the browser in a headless/kiosk state. If using Chromium, you must include the following flags to prevent memory crashes and force EGL/VA-API:
   `--enable-accelerated-video-decode --use-gl=egl --disable-dev-shm-usage --js-flags="--max-old-space-size=256"`

**Failure to follow these constraints will result in Out-Of-Memory (OOM) crashes or fatal latency spikes on the target Intel Atom hardware.**