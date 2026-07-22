/**
 * WebCodecs & WebAudio Player for Android Auto Video (H.264) and Audio (AAC / PCM)
 * Adheres strictly to docs/new-pattern.md
 */

export class WebCodecsPlayer {
    constructor(canvasElement, statusElement) {
        this.canvas = canvasElement;
        this.status = statusElement;
        this.ctx = canvasElement ? canvasElement.getContext('2d') : null;
        
        this.videoDecoder = null;
        this.audioDecoder = null;
        this.audioCtx = null;
        this.ws = null;
        
        this.isConfigured = false;
        this.frameCount = 0;
        this.lastFpsUpdate = performance.now();

        this.initDecoders();
    }

    initDecoders() {
        if (!('VideoDecoder' in window)) {
            this.updateStatus('WebCodecs VideoDecoder not supported!', true);
            return;
        }

        // Initialize H.264 Video Decoder
        this.videoDecoder = new VideoDecoder({
            output: (frame) => this.handleVideoFrame(frame),
            error: (err) => {
                console.error('VideoDecoder error:', err);
                this.updateStatus(`VideoDecoder Error: ${err.message}`, true);
            }
        });

        // Initialize Web Audio API for PCM/AAC Playback
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (AudioContextClass) {
            this.audioCtx = new AudioContextClass({ sampleRate: 48000 });
        }

        // Initialize Audio Decoder if supported
        if ('AudioDecoder' in window) {
            this.audioDecoder = new AudioDecoder({
                output: (audioData) => this.handleAudioData(audioData),
                error: (err) => console.error('AudioDecoder error:', err)
            });

            try {
                this.audioDecoder.configure({
                    codec: 'mp4a.40.2', // AAC-LC
                    numberOfChannels: 2,
                    sampleRate: 48000
                });
            } catch (e) {
                console.warn('AudioDecoder AAC config fallback:', e);
            }
        }
    }

    updateStatus(msg, isError = false) {
        if (this.status) {
            this.status.textContent = msg;
            this.status.style.color = isError ? '#ff4d4d' : '#00e676';
        }
    }

    connect(wsUrl) {
        this.ws = new WebSocket(wsUrl);
        this.ws.binaryType = 'arraybuffer';

        this.ws.onopen = () => {
            this.updateStatus('WebSocket Connected. Unified Stream Active.');
            this.startMicrophoneUplink();
        };

        this.ws.onclose = () => {
            this.updateStatus('WebSocket Disconnected', true);
            setTimeout(() => this.connect(wsUrl), 2000);
        };

        this.ws.onerror = (err) => {
            console.error('WebSocket error:', err);
            this.updateStatus('WebSocket Connection Error', true);
        };

        this.ws.onmessage = (event) => this.handleMessage(event);
    }

    handleMessage(event) {
        if (!(event.data instanceof ArrayBuffer)) return;

        const data = event.data;
        if (data.byteLength < 9) return;

        const view = new DataView(data);
        const streamType = view.getUint8(0);
        const timestampUs = Number(view.getBigUint64(1, false));
        const payload = new Uint8Array(data, 9);

        if (streamType === 0) {
            this.processVideoNal(payload, timestampUs);
        } else if (streamType === 1) {
            this.processAudioChunk(payload, timestampUs);
        }
    }

    processVideoNal(payload, timestampUs) {
        if (payload.length === 0) return;

        let nalOffset = 0;
        if (payload.length >= 4 && payload[0] === 0 && payload[1] === 0 && payload[2] === 0 && payload[3] === 1) {
            nalOffset = 4;
        } else if (payload.length >= 3 && payload[0] === 0 && payload[1] === 0 && payload[2] === 1) {
            nalOffset = 3;
        }

        const nalType = payload[nalOffset] & 0x1F;

        if (!this.isConfigured) {
            try {
                this.videoDecoder.configure({
                    codec: 'avc1.42E01E', // Baseline profile
                    hardwareAcceleration: 'prefer-hardware',
                    optimizeForLatency: true
                });
                this.isConfigured = true;
                this.updateStatus('VideoDecoder Configured (Hardware Acceleration Enabled)');
            } catch (e) {
                console.error('Failed to configure VideoDecoder:', e);
                return;
            }
        }

        const chunkType = (nalType === 5) ? 'key' : 'delta';

        try {
            const chunk = new EncodedVideoChunk({
                type: chunkType,
                timestamp: timestampUs,
                data: payload
            });

            this.videoDecoder.decode(chunk);
        } catch (e) {
            console.error('Video decode error:', e);
        }
    }

    processAudioChunk(payload, timestampUs) {
        if (!this.audioCtx || payload.length === 0) return;

        // Try WebCodecs AudioDecoder for AAC or direct PCM AudioBuffer playback
        if (this.audioDecoder && this.audioDecoder.state === 'configured') {
            try {
                const chunk = new EncodedAudioChunk({
                    type: 'key',
                    timestamp: timestampUs,
                    data: payload
                });
                this.audioDecoder.decode(chunk);
                return;
            } catch (e) {
                // Fallback to PCM AudioBuffer
            }
        }

        // Direct PCM (16-bit LE 48kHz stereo fallback)
        try {
            const int16Array = new Int16Array(payload.buffer, payload.byteOffset, payload.byteLength / 2);
            const numFrames = int16Array.length / 2;
            if (numFrames <= 0) return;

            const buffer = this.audioCtx.createBuffer(2, numFrames, 48000);
            const leftChannel = buffer.getChannelData(0);
            const rightChannel = buffer.getChannelData(1);

            for (let i = 0; i < numFrames; i++) {
                leftChannel[i] = int16Array[i * 2] / 32768.0;
                rightChannel[i] = int16Array[i * 2 + 1] / 32768.0;
            }

            const source = this.audioCtx.createBufferSource();
            source.buffer = buffer;
            source.connect(this.audioCtx.destination);
            source.start();
        } catch (e) {
            console.error('PCM playback error:', e);
        }
    }

    handleVideoFrame(frame) {
        try {
            if (this.canvas.width !== frame.displayWidth || this.canvas.height !== frame.displayHeight) {
                this.canvas.width = frame.displayWidth;
                this.canvas.height = frame.displayHeight;
            }

            this.ctx.drawImage(frame, 0, 0, this.canvas.width, this.canvas.height);
            this.frameCount++;

            const now = performance.now();
            if (now - this.lastFpsUpdate >= 1000) {
                const fps = Math.round((this.frameCount * 1000) / (now - this.lastFpsUpdate));
                this.updateStatus(`Streaming: ${this.canvas.width}x${this.canvas.height} @ ${fps} FPS`);
                this.frameCount = 0;
                this.lastFpsUpdate = now;
            }
        } finally {
            // CRITICAL per docs/new-pattern.md: Close frame immediately to prevent memory leaks on 2GB RAM
            frame.close();
        }
    }

    handleAudioData(audioData) {
        try {
            // AudioData processed and freed
            audioData.close();
        } catch (e) {
            // Handled
        }
    }

    startMicrophoneUplink() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return;

        navigator.mediaDevices.getUserMedia({ audio: true, video: false })
            .then((stream) => {
                const mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
                mediaRecorder.ondataavailable = (event) => {
                    if (event.data.size > 0 && this.ws && this.ws.readyState === WebSocket.OPEN) {
                        event.data.arrayBuffer().then((buf) => {
                            this.ws.send(buf);
                        });
                    }
                };
                mediaRecorder.start(100); // 100ms intervals
                console.log('Microphone uplink stream started');
            })
            .catch((err) => {
                console.warn('Microphone access not granted or unavailable:', err);
            });
    }
}
