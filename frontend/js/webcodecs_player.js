/**
 * WebCodecs & WebAudio Player for Android Auto Video (H.264) and Audio (AAC / PCM)
 * Adheres strictly to docs/new-pattern.md
 */

export class WebCodecsPlayer {
  constructor(canvasElement, statusElement) {
    this.canvas = canvasElement;
    this.status = statusElement;
    this.ctx = canvasElement ? canvasElement.getContext("2d") : null;

    this.videoDecoder = null;
    this.audioDecoder = null;
    this.audioCtx = null;
    this.ws = null;

    this.isConfigured = false;
    this.hasReceivedKeyframe = false;
    this.latestSps = null;
    this.latestPps = null;
    this.frameCount = 0;
    this.lastFpsUpdate = performance.now();
    this.mediaRecorder = null;
    this.micStream = null;
    this.streamConfigs = {};

    // Active video transport mode — updated from stream_config JSON
    this.videoTransport = "h264"; // default until server reports otherwise

    // WebGL YUV renderer — lazily initialized on first yuv420 frame
    this._glRenderer = null;

    this.initDecoders();
  }

  initDecoders() {
    if (!("VideoDecoder" in window)) {
      this.updateStatus("WebCodecs VideoDecoder not supported!", true);
      return;
    }

    // Initialize H.264 Video Decoder
    this.initVideoDecoder();

    // Initialize Web Audio API for PCM/AAC Playback
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (AudioContextClass) {
      this.audioCtx = new AudioContextClass({ sampleRate: 48000 });
    }

    // Initialize Audio Decoder if supported
    if ("AudioDecoder" in window) {
      this.audioDecoder = new AudioDecoder({
        output: (audioData) => this.handleAudioData(audioData),
        error: (err) => {
          const ch =
            this.lastSubmittedAudioChunk &&
            this.lastSubmittedAudioChunk.channelId
              ? this.lastSubmittedAudioChunk.channelId
              : "?";
          console.error(
            `[WebCodecsPlayer ch${ch}] AudioDecoder decoding error (switching to WebAudio PCM fallback):`,
            err,
          );
          if (
            this.lastSubmittedAudioChunk &&
            this.lastSubmittedAudioChunk.payload
          ) {
            const p = this.lastSubmittedAudioChunk.payload;
            const hexHeader = Array.from(p.subarray(0, 64))
              .map((b) => b.toString(16).padStart(2, "0"))
              .join("");
            console.error(
              `[WebCodecsPlayer ch${ch}] ❌ FAILING AUDIO FRAME (len=${p.length}): hex=${hexHeader}`,
            );
          }
          try {
            this.audioDecoder.close();
          } catch (e) {}
          this.audioDecoder = null;
        },
      });
    }
  }

  initVideoDecoder() {
    if (!("VideoDecoder" in window)) return;
    try {
      if (this.videoDecoder) {
        try {
          this.videoDecoder.close();
        } catch (e) {}
      }
    } catch (e) {}

    this.videoDecoder = new VideoDecoder({
      output: (frame) => this.handleVideoFrame(frame),
      error: (err) => {
        const hexHeader = this.lastSubmittedVideoChunk
          ? Array.from(this.lastSubmittedVideoChunk.data.subarray(0, 64))
              .map((b) => b.toString(16).padStart(2, "0"))
              .join(" ")
          : "none";
        console.error(
          `[WebCodecsPlayer] ❌ VideoDecoder fatal error (${err.name}: ${err.message})! State=${this.videoDecoder ? this.videoDecoder.state : "null"}`,
        );
        console.error(
          `[WebCodecsPlayer] ❌ FAILING NAL CHUNK (type=${this.lastSubmittedVideoChunk ? this.lastSubmittedVideoChunk.type : "?"}, len=${this.lastSubmittedVideoChunk ? this.lastSubmittedVideoChunk.data.length : 0}): hex=[ ${hexHeader} ]`,
        );
        this.updateStatus(`VideoDecoder Error: ${err.message}`, true);
        this.hasReceivedKeyframe = false;
        this.isConfigured = false;
      },
    });
    this.isConfigured = false;
    this.hasReceivedKeyframe = false;
    this.lastSubmittedVideoChunk = null;
  }

  applyStreamConfigs(streams) {
    if (!streams) return;
    for (const [chId, config] of Object.entries(streams)) {
      if (config.media_type === "AUDIO" && config.codec !== "PCM") {
        try {
          // Re-create AudioDecoder if closed or null on early error
          if (!this.audioDecoder && "AudioDecoder" in window) {
            this.audioDecoder = new AudioDecoder({
              output: (audioData) => this.handleAudioData(audioData),
              error: (err) => {
                const ch =
                  this.lastSubmittedAudioChunk &&
                  this.lastSubmittedAudioChunk.channelId
                    ? this.lastSubmittedAudioChunk.channelId
                    : "?";
                console.error(
                  `[WebCodecsPlayer ch${ch}] AudioDecoder decoding error (switching to WebAudio fallback):`,
                  err,
                );
                if (
                  this.lastSubmittedAudioChunk &&
                  this.lastSubmittedAudioChunk.payload
                ) {
                  const p = this.lastSubmittedAudioChunk.payload;
                  const hexHeader = Array.from(p.subarray(0, 64))
                    .map((b) => b.toString(16).padStart(2, "0"))
                    .join("");
                  console.error(
                    `[WebCodecsPlayer ch${ch}] ❌ FAILING AUDIO FRAME (len=${p.length}): hex=${hexHeader}`,
                  );
                }
                try {
                  this.audioDecoder.close();
                } catch (e) {}
                this.audioDecoder = null;
              },
            });
          }

          if (this.audioDecoder) {
            // For ADTS AAC streams, description MUST be undefined so AudioDecoder expects ADTS container headers
            const desc =
              config.audio_format === "aac_adts"
                ? undefined
                : Array.isArray(config.description)
                  ? new Uint8Array(config.description)
                  : undefined;
            this.audioDecoder.configure({
              codec: config.codec || "mp4a.40.2",
              numberOfChannels: config.channels || 2,
              sampleRate: config.sampleRate || 48000,
              description: desc,
            });
            console.info(
              `[WebCodecsPlayer ch${chId}] AudioDecoder configured for (${config.codec}, ${config.sampleRate}Hz, ${config.channels}ch, format=${config.audio_format})`,
            );
          }
        } catch (e) {
          console.warn(
            `[WebCodecsPlayer ch${chId}] AudioDecoder config failed:`,
            e,
          );
        }
      }
    }
  }

  updateStatus(msg, isError = false) {
    if (this.status) {
      this.status.textContent = msg;
      this.status.style.color = isError ? "#ff4d4d" : "#00e676";
    }
    if (isError) {
      this.sendLogToBackend("WARNING", msg);
    }
  }

  sendLogToBackend(level, message, module = "webcodecs_player") {
    try {
      fetch("/api/system/client_log", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ level, message, module }),
      }).catch(() => {});
    } catch (e) {}
  }

  connect(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.ws.binaryType = "arraybuffer";

    this.ws.onopen = async () => {
      this.updateStatus("WebSocket Connected. Unified Stream Active.");
      // Send client capabilities for auto-negotiation
      const caps = await this._probeCapabilities();
      this.ws.send(JSON.stringify(caps));
      console.info("[WebCodecsPlayer] Sent client_capabilities:", caps);
    };

    this.ws.onclose = () => {
      this.updateStatus("WebSocket Disconnected", true);
      this.stopMicrophoneUplink();
      setTimeout(() => this.connect(wsUrl), 2000);
    };

    this.ws.onerror = (err) => {
      console.error("WebSocket error:", err);
      this.updateStatus("WebSocket Connection Error", true);
    };

    this.ws.onmessage = (event) => this.handleMessage(event);
  }

  async _probeCapabilities() {
    const caps = {
      type: "client_capabilities",
      webcodecs_h264_hw: false,
      webgl: !!document.createElement("canvas").getContext("webgl"),
      webgl2: !!document.createElement("canvas").getContext("webgl2"),
      create_image_bitmap: typeof createImageBitmap === "function",
      max_bandwidth_mbps: null,
    };

    if ("VideoDecoder" in window) {
      try {
        const result = await VideoDecoder.isConfigSupported({
          codec: "avc1.42E01E",
          hardwareAcceleration: "prefer-hardware",
        });
        caps.webcodecs_h264_hw = !!(result && result.supported);
      } catch (e) {
        caps.webcodecs_h264_hw = false;
      }
    }

    return caps;
  }

  handleMessage(event) {
    if (typeof event.data === "string") {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "stream_config") {
          console.info(
            "[WebCodecsPlayer] Received dynamic stream_config from backend:",
            msg.streams,
          );
          this.streamConfigs = msg.streams || {};
          this.applyStreamConfigs(this.streamConfigs);

          // Update active video transport and switch rendering path
          if (
            msg.video_transport &&
            msg.video_transport !== this.videoTransport
          ) {
            this.videoTransport = msg.video_transport;
            console.info(
              `[WebCodecsPlayer] Video transport switched to: '${this.videoTransport}'`,
            );
            this.sendLogToBackend(
              "INFO",
              `Video transport switched to '${this.videoTransport}'`,
              "webcodecs_player",
            );
            // Reset WebCodecs state when leaving h264 mode
            if (this.videoTransport !== "h264") {
              this.hasReceivedKeyframe = false;
              this.isConfigured = false;
            }
          }
        } else if (msg.type === "mic_control") {
          if (msg.enabled) {
            console.info(
              "[WebCodecsPlayer] Microphone uplink requested by phone -> Enabling microphone",
            );
            this.startMicrophoneUplink();
          } else {
            console.info(
              "[WebCodecsPlayer] Microphone uplink released by phone -> Disabling microphone",
            );
            this.stopMicrophoneUplink();
          }
        }
      } catch (e) {
        console.warn("[WebCodecsPlayer] Error parsing JSON text message:", e);
      }
      return;
    }

    if (!(event.data instanceof ArrayBuffer)) return;

    const data = event.data;
    if (data.byteLength < 9) return;

    const view = new DataView(data);
    const channelId = view.getUint8(0);
    const rawTsUs = Number(view.getBigUint64(1, false));
    const timestampUs =
      Number.isFinite(rawTsUs) && rawTsUs >= 0 && rawTsUs <= 9007199254740991
        ? rawTsUs
        : Math.floor(performance.now() * 1000);
    const payload = new Uint8Array(data, 9);
    const config = this.streamConfigs[String(channelId)];

    if (!this.channelMsgCount) this.channelMsgCount = {};
    const chKey = String(channelId);
    this.channelMsgCount[chKey] = (this.channelMsgCount[chKey] || 0) + 1;
    const chCount = this.channelMsgCount[chKey];

    if (chCount <= 5 || chCount % 50 === 0) {
      console.info(
        `[WebCodecsPlayer ch${channelId}] WS Packet #${chCount} received: len=${data.byteLength} timestamp=${timestampUs}µs`,
      );
    }

    let isVideo = false;
    let isAudio = false;

    if (config && config.media_type) {
      isVideo = config.media_type === "VIDEO";
      isAudio = config.media_type === "AUDIO";
    } else {
      // Pure content inspection fallback (zero hardcoded channel IDs)
      const isH264Nal =
        payload.length >= 4 &&
        payload[0] === 0 &&
        payload[1] === 0 &&
        (payload[2] === 1 || (payload[2] === 0 && payload[3] === 1));
      if (isH264Nal) {
        isVideo = true;
      } else {
        isAudio = true;
      }
    }

    if (isVideo) {
      if (
        this.videoTransport === "mjpeg" ||
        this.videoTransport === "mjpeg-ffmpeg" ||
        this.videoTransport === "webp"
      ) {
        this._renderImageBitmap(payload, this.videoTransport);
      } else if (this.videoTransport === "yuv420") {
        this._renderYuv420(payload, timestampUs, channelId);
      } else if (this.videoTransport === "rgba") {
        this._renderRgba(payload, channelId);
      } else {
        // Default: h264 via WebCodecs VideoDecoder
        const hasStartCode =
          payload.length >= 4 &&
          payload[0] === 0 &&
          payload[1] === 0 &&
          (payload[2] === 1 || (payload[2] === 0 && payload[3] === 1));
        if (!hasStartCode) {
          console.info(
            `[WebCodecsPlayer ch${channelId}] Dropping AVMediaIndication / non-Annex-B metadata packet (${payload.length} bytes)`,
          );
          return;
        }
        this.processVideoNal(payload, timestampUs, channelId, config);
      }
    } else if (isAudio) {
      this.processAudioChunk(payload, timestampUs, channelId, config);
    }
  }

  processVideoNal(payload, timestampUs, channelId = 3, config = null) {
    if (payload.length === 0) return;

    let nalOffset = 0;
    if (
      payload.length >= 4 &&
      payload[0] === 0 &&
      payload[1] === 0 &&
      payload[2] === 0 &&
      payload[3] === 1
    ) {
      nalOffset = 4;
    } else if (
      payload.length >= 3 &&
      payload[0] === 0 &&
      payload[1] === 0 &&
      payload[2] === 1
    ) {
      nalOffset = 3;
    }

    const nalType = payload[nalOffset] & 0x1f;
    const nalName =
      nalType === 5
        ? "IDR-Keyframe"
        : nalType === 7
          ? "SPS"
          : nalType === 8
            ? "PPS"
            : "P-DeltaFrame";

    if (!this.receivedNalCount) this.receivedNalCount = 0;
    this.receivedNalCount++;

    if (
      this.receivedNalCount <= 5 ||
      nalType === 5 ||
      this.receivedNalCount % 50 === 0
    ) {
      console.info(
        `[WebCodecsPlayer ch${channelId}] NAL Unit #${this.receivedNalCount} parsed: nalType=${nalType} (${nalName}), payloadLen=${payload.length}`,
      );
    }

    // 1. Buffer Parameter Sets (SPS / PPS)
    if (nalType === 7) {
      this.latestSps = payload;
      console.info(
        `[WebCodecsPlayer ch${channelId}] Stored H.264 SPS parameter set (${payload.length} bytes)`,
      );
      return;
    }
    if (nalType === 8) {
      this.latestPps = payload;
      console.info(
        `[WebCodecsPlayer ch${channelId}] Stored H.264 PPS parameter set (${payload.length} bytes)`,
      );
      return;
    }

    // 2. Keyframe Gate: Drop P-frames (nalType 1, 2, 3, 4) until an IDR frame (nalType 5) arrives
    if (!this.hasReceivedKeyframe) {
      if (nalType === 5) {
        this.hasReceivedKeyframe = true;
        console.info(
          `[WebCodecsPlayer ch${channelId}] IDR Keyframe (nalType=5, byte=0x${payload[nalOffset].toString(16)}) received -> Unlocking VideoDecoder`,
        );
      } else {
        if (!this.droppedDeltaCount) this.droppedDeltaCount = 0;
        this.droppedDeltaCount++;
        if (this.droppedDeltaCount <= 5 || this.droppedDeltaCount % 30 === 0) {
          console.warn(
            `[WebCodecsPlayer ch${channelId}] Dropping pre-keyframe delta NAL unit #${this.droppedDeltaCount} (nalType=${nalType}, byte=0x${payload[nalOffset].toString(16)}) prior to first IDR frame`,
          );
        }
        return;
      }
    }

    // 3. Configure VideoDecoder if not yet configured
    if (!this.isConfigured) {
      if (!this.videoDecoder || this.videoDecoder.state === "closed") {
        console.info(
          `[WebCodecsPlayer ch${channelId}] VideoDecoder instance is null/closed. Initializing...`,
        );
        this.initVideoDecoder();
      }

      const codecStr = config && config.codec ? config.codec : "avc1.42E01E";
      const chosenConfig = { codec: codecStr, optimizeForLatency: true };

      try {
        this.videoDecoder.configure(chosenConfig);
        this.isConfigured = true;
        console.info(
          `[WebCodecsPlayer ch${channelId}] VideoDecoder configured synchronously with ${codecStr}.`,
        );
        this.updateStatus(`VideoDecoder Configured (${codecStr})`);
      } catch (e) {
        console.error(
          `[WebCodecsPlayer ch${channelId}] Failed to configure VideoDecoder with ${codecStr}:`,
          e,
        );
        this.updateStatus(`VideoDecoder Error: ${e.message}`, true);
        return;
      }
    }

    // 4. Assemble Chunk Payload (Prepend SPS/PPS to IDR keyframe units)
    let chunkData = payload;
    let chunkType = "delta";

    if (nalType === 5) {
      chunkType = "key";
      if (this.latestSps || this.latestPps) {
        const parts = [];
        if (this.latestSps) parts.push(this.latestSps);
        if (this.latestPps) parts.push(this.latestPps);
        parts.push(payload);

        let totalLen = 0;
        for (const p of parts) totalLen += p.length;
        const combined = new Uint8Array(totalLen);
        let offset = 0;
        for (const p of parts) {
          combined.set(p, offset);
          offset += p.length;
        }
        chunkData = combined;
      }
    }

    try {
      const chunk = new EncodedVideoChunk({
        type: chunkType,
        timestamp: timestampUs,
        data: chunkData,
      });

      this.lastSubmittedVideoChunk = {
        type: chunkType,
        data: chunkData,
        nalType,
      };
      this.videoDecoder.decode(chunk);
    } catch (e) {
      const hexHeader = Array.from(chunkData.subarray(0, 64))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join(" ");
      console.error(
        `[WebCodecsPlayer ch${channelId}] ❌ Synchronous Video decode exception (${e.name}: ${e.message})`,
      );
      console.error(
        `[WebCodecsPlayer ch${channelId}] ❌ FAILING NAL CHUNK (type=${chunkType}, nalType=${nalType}, len=${chunkData.length}): hex=[ ${hexHeader} ]`,
      );
      this.hasReceivedKeyframe = false;
      this.isConfigured = false;
      if (this.videoDecoder && this.videoDecoder.state !== "closed") {
        try {
          this.videoDecoder.close();
        } catch (closeErr) {}
      }
    }
  }

  ensureAudioContext() {
    if (!this.audioCtx) {
      const AudioContextClass =
        window.AudioContext || window.webkitAudioContext;
      if (AudioContextClass) {
        this.audioCtx = new AudioContextClass({ sampleRate: 48000 });
      }
    }
    if (this.audioCtx && this.audioCtx.state === "suspended") {
      this.audioCtx.resume();
    }
    return this.audioCtx;
  }

  playAudioBuffer(buffer) {
    const audioCtx = this.ensureAudioContext();
    if (!audioCtx || !buffer) return;

    const currentTime = audioCtx.currentTime;
    if (!this.nextAudioStartTime || this.nextAudioStartTime < currentTime) {
      this.nextAudioStartTime = currentTime + 0.03; // 30ms initial safety jitter buffer
    }

    const source = audioCtx.createBufferSource();
    source.buffer = buffer;
    source.connect(audioCtx.destination);
    source.start(this.nextAudioStartTime);
    this.nextAudioStartTime += buffer.duration;
  }

  processAudioChunk(payload, timestampUs, channelId = 4, config = null) {
    if (!this.ensureAudioContext() || payload.length === 0) return;

    // Ensure 16-bit aligned Uint8Array for Int16Array conversion (prevent RangeError)
    const alignedBytes =
      payload.byteOffset % 2 === 0 ? payload : payload.slice();

    // Dynamic PCM check using stream_config metadata (zero hardcoded channel IDs)
    const isExplicitPcmCodec =
      config &&
      (config.codec === "PCM" ||
        config.codec === "MEDIA_CODEC_AUDIO_PCM" ||
        config.audio_format === "pcm");

    // Check for AAC ADTS 12-bit sync word (0xFFF) with strict layer and frame_length validation
    let isAdtsAac = false;
    let aacPayload = alignedBytes;
    let adtsFrameCopy = null;

    if (
      !isExplicitPcmCodec &&
      alignedBytes.length >= 7 &&
      alignedBytes[0] === 0xff &&
      (alignedBytes[1] & 0xf6) === 0xf0
    ) {
      const mpegLayer = (alignedBytes[1] & 0x06) >> 1; // Must be 00 for AAC
      const frameLength =
        ((alignedBytes[3] & 0x03) << 11) |
        (alignedBytes[4] << 3) |
        ((alignedBytes[5] & 0xe0) >> 5);

      // Validate strict ADTS header invariants (mpegLayer === 0 and frameLength bounds)
      if (
        mpegLayer === 0 &&
        frameLength >= 7 &&
        frameLength <= alignedBytes.length
      ) {
        isAdtsAac = true;
        const protectionAbsent = alignedBytes[1] & 0x01;
        const headerLength = protectionAbsent ? 7 : 9;

        // Strip ADTS header for WebCodecs AudioDecoder raw AAC elementary stream
        if (frameLength > headerLength) {
          aacPayload = alignedBytes.subarray(headerLength, frameLength);
        }

        // Create copied ArrayBuffer starting at 0xFFF9 ADTS header for WebAudio decodeAudioData
        const adtsSlice = alignedBytes.subarray(0, frameLength);
        adtsFrameCopy = adtsSlice.slice().buffer;
      }
    }

    // Submit to WebCodecs AudioDecoder ONLY for verified AAC ADTS streams
    if (
      !isExplicitPcmCodec &&
      isAdtsAac &&
      this.audioDecoder &&
      this.audioDecoder.state === "configured"
    ) {
      try {
        this.lastSubmittedAudioChunk = {
          payload: aacPayload,
          channelId: channelId,
        };
        const chunk = new EncodedAudioChunk({
          type: "key",
          timestamp: timestampUs,
          data: aacPayload,
        });
        this.audioDecoder.decode(chunk);
        return;
      } catch (e) {
        const hexHeader = Array.from(aacPayload.subarray(0, 64))
          .map((b) => b.toString(16).padStart(2, "0"))
          .join("");
        console.warn(
          `[WebCodecsPlayer ch${channelId}] AudioDecoder submit exception:`,
          e,
          `len=${aacPayload.length}, hex=${hexHeader}`,
        );
      }
    }

    // Web Audio API decodeAudioData for ADTS AAC frames with smooth queueing (prevents PCM clicks & clippy gaps)
    if (isAdtsAac) {
      if (this.audioCtx && adtsFrameCopy) {
        this.audioCtx
          .decodeAudioData(adtsFrameCopy)
          .then((decodedBuffer) => {
            this.playAudioBuffer(decodedBuffer);
          })
          .catch((err) => {
            const hexHeader = Array.from(alignedBytes.subarray(0, 64))
              .map((b) => b.toString(16).padStart(2, "0"))
              .join("");
            console.error(
              `[WebCodecsPlayer ch${channelId}] ❌ FAILING WEBAUDIO ADTS FRAME (len=${alignedBytes.length}):`,
              err,
              `hex=${hexHeader}`,
            );
          });
      }
      return;
    }

    // Direct PCM (supports 48kHz stereo & 16kHz mono dynamically from stream_config with smooth queueing)
    try {
      const sampleRate =
        config && config.sampleRate ? config.sampleRate : 48000;
      const numChannels = config && config.channels ? config.channels : 2;
      const int16Array = new Int16Array(
        alignedBytes.buffer,
        alignedBytes.byteOffset,
        Math.floor(alignedBytes.byteLength / 2),
      );

      if (numChannels === 1) {
        const numFrames = int16Array.length;
        if (numFrames <= 0) return;
        const buffer = this.audioCtx.createBuffer(1, numFrames, sampleRate);
        const channelData = buffer.getChannelData(0);
        for (let i = 0; i < numFrames; i++) {
          channelData[i] = int16Array[i] / 32768.0;
        }
        this.playAudioBuffer(buffer);
      } else {
        const numFrames = Math.floor(int16Array.length / 2);
        if (numFrames <= 0) return;

        const buffer = this.audioCtx.createBuffer(2, numFrames, sampleRate);
        const leftChannel = buffer.getChannelData(0);
        const rightChannel = buffer.getChannelData(1);

        for (let i = 0; i < numFrames; i++) {
          leftChannel[i] = int16Array[i * 2] / 32768.0;
          rightChannel[i] = int16Array[i * 2 + 1] / 32768.0;
        }

        this.playAudioBuffer(buffer);
      }
    } catch (e) {
      console.error(`[WebCodecsPlayer ch${channelId}] PCM playback error:`, e);
    }
  }

  handleVideoFrame(frame) {
    try {
      if (
        this.canvas.width !== frame.displayWidth ||
        this.canvas.height !== frame.displayHeight
      ) {
        this.canvas.width = frame.displayWidth;
        this.canvas.height = frame.displayHeight;
      }

      this.ctx.drawImage(frame, 0, 0, this.canvas.width, this.canvas.height);
      this._updateFps();
    } finally {
      // CRITICAL per docs/new-pattern.md: Close frame immediately to prevent memory leaks on 2GB RAM
      frame.close();
    }
  }

  // ------------------------------------------------------------------
  // MJPEG / WebP rendering  (createImageBitmap path)
  // ------------------------------------------------------------------

  _renderImageBitmap(payload, mimeSubtype) {
    const mimeType = mimeSubtype === "webp" ? "image/webp" : "image/jpeg";
    const blob = new Blob([payload], { type: mimeType });
    createImageBitmap(blob)
      .then((bitmap) => {
        if (
          this.canvas.width !== bitmap.width ||
          this.canvas.height !== bitmap.height
        ) {
          this.canvas.width = bitmap.width;
          this.canvas.height = bitmap.height;
        }
        this.ctx.drawImage(bitmap, 0, 0, this.canvas.width, this.canvas.height);
        bitmap.close();
        this._updateFps();
      })
      .catch((err) => {
        console.warn(`[WebCodecsPlayer] ${mimeSubtype} decode error:`, err);
      });
  }

  // ------------------------------------------------------------------
  // YUV420 rendering  (WebGL BT.601 YUV→RGB shader path)
  // ------------------------------------------------------------------

  _initWebGl() {
    if (this._glRenderer) return;
    try {
      const gl =
        this.canvas.getContext("webgl") ||
        this.canvas.getContext("experimental-webgl");
      if (!gl) {
        console.warn("[WebCodecsPlayer] WebGL not available for YUV renderer");
        return;
      }

      const vsSource = `
                attribute vec2 aPosition;
                varying vec2 vTexCoord;
                void main() {
                    vTexCoord = aPosition * 0.5 + 0.5;
                    vTexCoord.y = 1.0 - vTexCoord.y;
                    gl_Position = vec4(aPosition, 0.0, 1.0);
                }`;

      const fsSource = `
                precision mediump float;
                varying vec2 vTexCoord;
                uniform sampler2D yTex;
                uniform sampler2D uTex;
                uniform sampler2D vTex;
                void main() {
                    float y = texture2D(yTex, vTexCoord).r;
                    float u = texture2D(uTex, vTexCoord).r - 0.5;
                    float v = texture2D(vTex, vTexCoord).r - 0.5;
                    gl_FragColor = vec4(
                        clamp(y + 1.402 * v,          0.0, 1.0),
                        clamp(y - 0.344 * u - 0.714 * v, 0.0, 1.0),
                        clamp(y + 1.772 * u,          0.0, 1.0),
                        1.0
                    );
                }`;

      const compile = (type, src) => {
        const s = gl.createShader(type);
        gl.shaderSource(s, src);
        gl.compileShader(s);
        if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
          throw new Error(gl.getShaderInfoLog(s));
        return s;
      };

      const prog = gl.createProgram();
      gl.attachShader(prog, compile(gl.VERTEX_SHADER, vsSource));
      gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, fsSource));
      gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS))
        throw new Error(gl.getProgramInfoLog(prog));

      // Fullscreen quad
      const buf = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      gl.bufferData(
        gl.ARRAY_BUFFER,
        new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
        gl.STATIC_DRAW,
      );

      const mkTex = () => {
        const t = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, t);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        return t;
      };

      this._glRenderer = {
        gl,
        prog,
        buf,
        yTex: mkTex(),
        uTex: mkTex(),
        vTex: mkTex(),
        aPos: gl.getAttribLocation(prog, "aPosition"),
        uY: gl.getUniformLocation(prog, "yTex"),
        uU: gl.getUniformLocation(prog, "uTex"),
        uV: gl.getUniformLocation(prog, "vTex"),
      };
      console.info("[WebCodecsPlayer] WebGL YUV→RGB renderer initialized");
    } catch (e) {
      console.error("[WebCodecsPlayer] WebGL init failed:", e);
      this._glRenderer = null;
    }
  }

  _renderYuv420(payload, timestampUs, channelId) {
    if (payload.length < 12) return;
    const dv = new DataView(payload.buffer, payload.byteOffset);
    const width = dv.getUint32(0);
    const height = dv.getUint32(4);
    // flags = dv.getUint32(8) — reserved

    const ySize = width * height;
    const uvSize = (width >> 1) * (height >> 1);
    if (payload.length < 12 + ySize + uvSize * 2) {
      console.warn(
        `[WebCodecsPlayer ch${channelId}] YUV420 frame too short: ${payload.length} vs expected ${12 + ySize + uvSize * 2}`,
      );
      return;
    }

    const yPlane = new Uint8Array(
      payload.buffer,
      payload.byteOffset + 12,
      ySize,
    );
    const uPlane = new Uint8Array(
      payload.buffer,
      payload.byteOffset + 12 + ySize,
      uvSize,
    );
    const vPlane = new Uint8Array(
      payload.buffer,
      payload.byteOffset + 12 + ySize + uvSize,
      uvSize,
    );

    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }

    this._initWebGl();
    const r = this._glRenderer;
    if (!r) {
      // WebGL unavailable — fall back to rgba-style rendering is not possible here;
      // log and skip. The auto-negotiation should have avoided yuv420 in this case.
      return;
    }

    const { gl, prog, buf, yTex, uTex, vTex, aPos, uY, uU, uV } = r;
    gl.viewport(0, 0, width, height);
    gl.useProgram(prog);

    const uploadTex = (tex, plane, w, h) => {
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.LUMINANCE,
        w,
        h,
        0,
        gl.LUMINANCE,
        gl.UNSIGNED_BYTE,
        plane,
      );
    };

    gl.activeTexture(gl.TEXTURE0);
    uploadTex(yTex, yPlane, width, height);
    gl.activeTexture(gl.TEXTURE1);
    uploadTex(uTex, uPlane, width >> 1, height >> 1);
    gl.activeTexture(gl.TEXTURE2);
    uploadTex(vTex, vPlane, width >> 1, height >> 1);

    gl.uniform1i(uY, 0);
    gl.uniform1i(uU, 1);
    gl.uniform1i(uV, 2);

    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);

    this._updateFps();
  }

  // ------------------------------------------------------------------
  // RGBA rendering  (putImageData path — zero dependencies)
  // ------------------------------------------------------------------

  _renderRgba(payload, channelId) {
    if (payload.length < 12) return;
    const dv = new DataView(payload.buffer, payload.byteOffset);
    const width = dv.getUint32(0);
    const height = dv.getUint32(4);
    // flags = dv.getUint32(8) — reserved

    const expectedBytes = 12 + width * height * 4;
    if (payload.length < expectedBytes) {
      console.warn(
        `[WebCodecsPlayer ch${channelId}] RGBA frame too short: ${payload.length} vs expected ${expectedBytes}`,
      );
      return;
    }

    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }

    const rgbaData = new Uint8ClampedArray(
      payload.buffer,
      payload.byteOffset + 12,
      width * height * 4,
    );
    const imageData = new ImageData(rgbaData, width, height);
    this.ctx.putImageData(imageData, 0, 0);
    this._updateFps();
  }

  // ------------------------------------------------------------------
  // FPS tracking  (shared across all rendering paths)
  // ------------------------------------------------------------------

  _updateFps() {
    this.frameCount++;
    const now = performance.now();
    if (now - this.lastFpsUpdate >= 1000) {
      const fps = Math.round(
        (this.frameCount * 1000) / (now - this.lastFpsUpdate),
      );
      this.updateStatus(
        `Streaming [${this.videoTransport}]: ${this.canvas.width}x${this.canvas.height} @ ${fps} FPS`,
      );
      this.frameCount = 0;
      this.lastFpsUpdate = now;
    }
  }

  handleAudioData(audioData) {
    try {
      if (!this.audioCtx) return;
      const numberOfChannels = audioData.numberOfChannels;
      const sampleRate = audioData.sampleRate;
      const numberOfFrames = audioData.numberOfFrames;
      const buffer = this.audioCtx.createBuffer(
        numberOfChannels,
        numberOfFrames,
        sampleRate,
      );

      for (let channel = 0; channel < numberOfChannels; channel++) {
        const options = { planeIndex: channel, format: "f32-planar" };
        audioData.copyTo(buffer.getChannelData(channel), options);
      }

      this.playAudioBuffer(buffer);
    } catch (e) {
      console.warn("[WebCodecsPlayer] AudioData playback error:", e);
    } finally {
      audioData.close();
    }
  }

  async startMicrophoneUplink() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;
    if (this.micStream) return; // Already running

    this.micPcmAccumulator = [];

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, sampleRate: 16000 },
        video: false,
      });
      this.micStream = stream;

      const AudioCtxClass = window.AudioContext || window.webkitAudioContext;
      this.micAudioCtx = new AudioCtxClass({ sampleRate: 16000 });
      this.micSource = this.micAudioCtx.createMediaStreamSource(stream);

      const workletCode = `
                class MicProcessor extends AudioWorkletProcessor {
                    constructor() {
                        super();
                        this.buffer = [];
                    }
                    process(inputs) {
                        const input = inputs[0];
                        if (input && input[0]) {
                            const channel = input[0];
                            for (let i = 0; i < channel.length; i++) {
                                let s = Math.max(-1, Math.min(1, channel[i]));
                                this.buffer.push(s < 0 ? s * 0x8000 : s * 0x7FFF);
                            }
                            while (this.buffer.length >= 320) {
                                const samples = this.buffer.splice(0, 320);
                                const int16 = new Int16Array(samples);
                                this.port.postMessage(int16.buffer, [int16.buffer]);
                            }
                        }
                        return true;
                    }
                }
                registerProcessor('mic-processor', MicProcessor);
            `;

      try {
        const blob = new Blob([workletCode], {
          type: "application/javascript",
        });
        const workletUrl = URL.createObjectURL(blob);
        await this.micAudioCtx.audioWorklet.addModule(workletUrl);
        this.micWorkletNode = new AudioWorkletNode(
          this.micAudioCtx,
          "mic-processor",
        );
        this.micWorkletNode.port.onmessage = (e) => {
          if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(e.data);
          }
        };
        this.micSource.connect(this.micWorkletNode);
        console.log(
          "[WebCodecsPlayer] Microphone uplink started via AudioWorkletNode (Raw 16kHz 16-bit Mono PCM, 20ms chunks)",
        );
      } catch (workletErr) {
        console.warn(
          "[WebCodecsPlayer] AudioWorklet fallback to ScriptProcessor:",
          workletErr,
        );
        this.micProcessor = this.micAudioCtx.createScriptProcessor(2048, 1, 1);
        const CHUNK_SAMPLES = 320;
        this.micProcessor.onaudioprocess = (e) => {
          if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
          const float32 = e.inputBuffer.getChannelData(0);
          for (let i = 0; i < float32.length; i++) {
            let s = Math.max(-1, Math.min(1, float32[i]));
            this.micPcmAccumulator.push(s < 0 ? s * 0x8000 : s * 0x7fff);
          }
          while (this.micPcmAccumulator.length >= CHUNK_SAMPLES) {
            const samples = this.micPcmAccumulator.splice(0, CHUNK_SAMPLES);
            const int16 = new Int16Array(samples);
            this.ws.send(int16.buffer);
          }
        };
        this.micSource.connect(this.micProcessor);
        this.micProcessor.connect(this.micAudioCtx.destination);
      }
    } catch (err) {
      console.warn("Microphone access not granted or unavailable:", err);
    }
  }

  stopMicrophoneUplink() {
    this.micPcmAccumulator = [];
    if (this.micWorkletNode) {
      try {
        this.micWorkletNode.disconnect();
      } catch (e) {}
      this.micWorkletNode = null;
    }
    if (this.micProcessor) {
      try {
        this.micProcessor.disconnect();
        this.micProcessor.onaudioprocess = null;
      } catch (e) {}
      this.micProcessor = null;
    }
    if (this.micSource) {
      try {
        this.micSource.disconnect();
      } catch (e) {}
      this.micSource = null;
    }
    if (this.micAudioCtx) {
      try {
        this.micAudioCtx.close();
      } catch (e) {}
      this.micAudioCtx = null;
    }
    if (this.micStream) {
      try {
        this.micStream.getTracks().forEach((track) => track.stop());
      } catch (e) {}
      this.micStream = null;
    }
    console.log("[WebCodecsPlayer] Microphone uplink stopped");
  }
}
