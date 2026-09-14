/**
 * diagnostic.js — Multimedia Diagnostics Widget for Web Frontend.
 */

export class DiagnosticWidget {
    constructor(containerId = 'diagnostics-drawer-content') {
        this.container = document.getElementById(containerId);
        this.ws = null;
        this.initDOM();
    }

    initDOM() {
        if (!this.container) return;

        this.container.innerHTML = `
            <div class="diag-container">
                <!-- Audio Diagnostics -->
                <div class="diag-card">
                    <div class="diag-card-title">🔊 Audio Pipeline Tests</div>
                    <div class="diag-btn-grid">
                        <button class="diag-btn" id="btn-diag-pcm440">▶ PCM Tone (440Hz)</button>
                        <button class="diag-btn" id="btn-diag-pcm1000">▶ PCM Tone (1kHz)</button>
                        <button class="diag-btn" id="btn-diag-aac">▶ AAC Chime</button>
                        <button class="diag-btn" id="btn-diag-mic">🎤 Mic Level Test</button>
                        <button class="diag-btn" id="btn-diag-cli-proc">⚙️ Subprocess Tone (Pull)</button>
                        <button class="diag-btn" id="btn-diag-in-proc">⚡ In-Process Tone (Pull)</button>
                    </div>

                    <div class="diag-vu-container">
                        <span class="diag-label">Mic Level:</span>
                        <div class="diag-vu-track">
                            <div class="diag-vu-bar" id="diag-vu-bar"></div>
                        </div>
                    </div>

                    <div class="diag-device-row">
                        <select id="diag-sink-select" class="styled-select" style="flex: 1;">
                            <option value="default">Default Audio Sink</option>
                        </select>
                        <button class="diag-btn diag-btn-apply" id="btn-diag-apply-sink">Apply Sink</button>
                    </div>
                </div>

                <!-- Video Diagnostics -->
                <div class="diag-card">
                    <div class="diag-card-title">🎬 Video & HW Acceleration</div>
                    <div class="diag-control-row">
                        <label class="diag-label">Transport:</label>
                        <select id="diag-transport-select" class="styled-select" style="flex: 1;">
                            <option value="mjpeg">MJPEG</option>
                            <option value="webp">WEBP</option>
                            <option value="yuv420">YUV420</option>
                            <option value="rgba">RGBA</option>
                            <option value="h264">H264 (Passthrough)</option>
                        </select>
                    </div>

                    <div class="diag-control-row" style="margin-top: 8px;">
                        <label class="diag-label">Decoder:</label>
                        <select id="diag-decoder-select" class="styled-select" style="flex: 1;">
                            <option value="auto">Auto (Negotiated)</option>
                            <option value="forced_v4l2">Hardware (V4L2)</option>
                            <option value="forced_vaapi">Hardware (VAAPI)</option>
                            <option value="forced_d3d11va">Hardware (Direct3D 11)</option>
                            <option value="forced_nvdec">Hardware (NVDEC)</option>
                            <option value="sw">Software (FFmpeg)</option>
                        </select>
                    </div>

                    <button class="diag-btn diag-btn-action" id="btn-diag-benchmark" style="margin-top: 10px; width: 100%;">
                        🚀 Run Video Transport Benchmark (2s)
                    </button>
                </div>

                <!-- Live Diagnostic Logs -->
                <div class="diag-card">
                    <div class="diag-card-title">📋 Diagnostic Output</div>
                    <div class="diag-console" id="diag-console">Ready.</div>
                </div>
            </div>
        `;

        this.bindEvents();
    }

    bindEvents() {
        document.getElementById('btn-diag-pcm440')?.addEventListener('click', () => {
            this.runTest('audio_pcm', { tone_hz: 440, duration_ms: 1500 });
        });
        document.getElementById('btn-diag-pcm1000')?.addEventListener('click', () => {
            this.runTest('audio_pcm', { tone_hz: 1000, duration_ms: 1500 });
        });
        document.getElementById('btn-diag-aac')?.addEventListener('click', () => {
            this.runTest('audio_aac', { duration_ms: 1500 });
        });
        document.getElementById('btn-diag-mic')?.addEventListener('click', () => {
            this.runTest('audio_mic', { duration_ms: 3000 });
        });
        document.getElementById('btn-diag-cli-proc')?.addEventListener('click', () => {
            this.runTest('audio_standalone_proc', { freq: 440, duration_sec: 2.0 });
        });
        document.getElementById('btn-diag-in-proc')?.addEventListener('click', () => {
            this.runTest('audio_in_process', { freq: 440, duration_sec: 2.0, push: false });
        });
        document.getElementById('btn-diag-apply-sink')?.addEventListener('click', () => {
            const sink = document.getElementById('diag-sink-select')?.value;
            this.runTest('audio_device_select', { sink });
        });
        document.getElementById('btn-diag-benchmark')?.addEventListener('click', () => {
            const transport = document.getElementById('diag-transport-select')?.value;
            const decoder = document.getElementById('diag-decoder-select')?.value;
            this.runTest('video_benchmark', { transport, decoder, duration_sec: 2.0, fps: 30 });
        });
    }

    open() {
        this.fetchCapabilities();
        this.connectWS();
    }

    close() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }

    async fetchCapabilities() {
        try {
            const res = await fetch('/api/media/diagnostic/capabilities');
            if (!res.ok) return;
            const data = await res.json();
            const sinks = data?.audio?.sinks || [];
            const select = document.getElementById('diag-sink-select');
            if (select && sinks.length > 0) {
                select.innerHTML = '<option value="default">Default Audio Sink</option>';
                sinks.forEach(s => {
                    const opt = document.createElement('option');
                    opt.value = s;
                    opt.textContent = s;
                    select.appendChild(opt);
                });
            }
        } catch (e) {
            this.log(`Notice: Capabilities fetch: ${e.message}`);
        }
    }

    connectWS() {
        if (this.ws) return;
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${window.location.host}/api/diagnostic/ws`;
        this.ws = new WebSocket(url);

        this.ws.onopen = () => {
            this.log('Connected to live diagnostic stream.');
        };

        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'test_started') {
                    this.log(`▶ Started: ${data.test_type}`);
                } else if (data.type === 'test_completed') {
                    const res = data.results || {};
                    this.log(`✔ Completed: ${res.test_type} (${res.status}) in ${res.elapsed_sec}s`);
                } else if (data.type === 'mic_level') {
                    const length = data.len || 0;
                    const pct = Math.min(100, Math.round((length / 1024.0) * 100));
                    const bar = document.getElementById('diag-vu-bar');
                    if (bar) bar.style.width = `${pct}%`;
                } else if (data.type === 'audio_frame_injected') {
                    this.log(`♫ Injected ${data.format} audio (${data.len} bytes)`);
                }
            } catch (e) {}
        };

        this.ws.onclose = () => {
            this.ws = null;
        };
    }

    async runTest(test_type, params) {
        this.log(`Triggering ${test_type}...`);
        try {
            const res = await fetch('/api/diagnostic/run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ test_type, params })
            });
            const data = await res.json();
            if (!res.ok) {
                this.log(`Error: ${data.message || 'Failed'}`);
            }
        } catch (e) {
            this.log(`Error triggering test: ${e.message}`);
        }
    }

    log(msg) {
        const consoleEl = document.getElementById('diag-console');
        if (!consoleEl) return;
        const line = document.createElement('div');
        line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
        consoleEl.appendChild(line);
        consoleEl.scrollTop = consoleEl.scrollHeight;
    }
}
