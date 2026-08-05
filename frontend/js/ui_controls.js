/**
 * UI Controls Manager: Arc Radial FAB Menu, Workflow Status Polling, Settings & Logs
 */

import { SettingsWidgets } from './settings_widgets.js';
import { BluetoothWidget } from './bluetooth_widget.js';

export class UIControls {
    constructor() {
        this.menuOpen = false;
        this.logWs = null;

        // Command Bar Elements
        this.btnHome = document.getElementById('btn-home');
        this.btnPlayPause = document.getElementById('btn-playpause');
        this.btnVolume = document.getElementById('btn-volume');
        this.btnMenu = document.getElementById('btn-menu');
        this.btnClose = document.getElementById('btn-close');

        // Status Indicator Dot
        this.statusDot = document.getElementById('status-dot');

        // Volume Popover Elements
        this.volumePopover = document.getElementById('volume-popover');
        this.volMute = document.getElementById('vol-mute');
        this.volDown = document.getElementById('vol-down');
        this.volUp = document.getElementById('vol-up');
        this.volLevelDisplay = document.getElementById('vol-level-display');

        // Arc FAB Menu Elements
        this.arcMenu = document.getElementById('arc-radial-menu');
        this.btnSettings = document.getElementById('fab-settings');
        this.btnBluetooth = document.getElementById('fab-bluetooth');
        this.btnWifi = document.getElementById('fab-wifi');
        this.btnLogs = document.getElementById('fab-logs');
        this.btnFullscreen = document.getElementById('fab-fullscreen');

        // Drawers
        this.drawerSettings = document.getElementById('settings-drawer');
        this.drawerBluetooth = document.getElementById('bluetooth-drawer');
        this.drawerLogs = document.getElementById('logs-drawer');
        this.btnCloseSettings = document.getElementById('close-settings');
        this.btnCloseBluetooth = document.getElementById('close-bluetooth');
        this.btnCloseLogs = document.getElementById('close-logs');

        // Screen & State
        this.disconnectedScreen = document.getElementById('disconnected-screen');
        this.logConsole = document.getElementById('log-console');
        this.bluetoothWidget = null;
        this.isVideoFocused = true;
        this.isPlaying = false;

        this.initEventListeners();
        this.initModuleStreams();
    }

    showToast(message, type = 'info', durationMs = 3500) {
        const container = document.getElementById('toast-container');
        if (!container) return;

        // RULE: Every new toast MUST remove previous active toasts immediately
        container.innerHTML = '';

        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        toast.textContent = message;
        container.appendChild(toast);

        requestAnimationFrame(() => toast.classList.add('show'));

        if (this.toastTimeout) clearTimeout(this.toastTimeout);
        this.toastTimeout = setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 350);
        }, durationMs);
    }

    setStatus(state) {
        if (!this.statusDot) return;
        this.statusDot.className = '';
        if (state === 'online') {
            this.statusDot.classList.add('status-online');
        } else if (state === 'connecting') {
            this.statusDot.classList.add('status-connecting');
        } else {
            this.statusDot.classList.add('status-offline');
        }
    }

    handleWorkflowStage(data) {
        const newIndex = data.stage_index ?? 0;

        // Disconnect reset: return to Stage 0 (Red)
        if (newIndex === 0) {
            if (this.currentStageIndex !== 0) {
                this.currentStageIndex = 0;
                this.setStatus('offline');
                this.showToast('Android Auto Disconnected', 'error');
            }
            return;
        }

        // Monotonic Stage Progression (only advance forward)
        if (newIndex > this.currentStageIndex) {
            this.currentStageIndex = newIndex;

            if (newIndex === 10) {
                this.setStatus('online'); // 🟢 Green
            } else {
                this.setStatus('connecting'); // 🟡 Yellow
            }

            if (data.toast_message) {
                const toastType = newIndex === 10 ? 'success' : 'warning';
                this.showToast(data.toast_message, toastType);
            }
        }
    }

    async toggleVideoFocus() {
        this.isVideoFocused = !this.isVideoFocused;
        const targetMode = this.isVideoFocused ? 'PROJECTED' : 'NATIVE';
        try {
            await fetch('/api/channels/focus', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode: targetMode })
            });
            if (this.isVideoFocused) {
                this.showToast('Resuming Android Auto Video Projection', 'info');
            } else {
                this.disconnectedScreen.classList.remove('hidden');
                this.showToast('Switched to Clock / Home (Video Suspended)', 'info');
            }
        } catch (e) {
            console.warn('Failed to toggle Video Focus:', e);
        }
    }

    async sendMediaKey(keyCode = 85) {
        try {
            await fetch('/api/channels/input/media', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ key_code: keyCode })
            });
            this.isPlaying = !this.isPlaying;
            const icon = document.getElementById('icon-playpause');
            if (icon) {
                icon.innerHTML = this.isPlaying
                    ? '<path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>' // Pause icon
                    : '<path d="M8 5v14l11-7z"/>'; // Play icon
            }
        } catch (e) {
            console.warn('Failed to send media key:', e);
        }
    }

    async controlVolume(action, volumeVal = null) {
        try {
            const res = await fetch('/api/media/volume', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: action, volume: volumeVal })
            });
            if (res.ok) {
                const data = await res.json();
                if (this.volLevelDisplay) {
                    this.volLevelDisplay.textContent = data.muted ? 'MUTE' : `${data.volume}%`;
                }
            }
        } catch (e) {}
    }

    initEventListeners() {
        const bindFab = (el, callback) => {
            if (!el) return;
            let lastTrigger = 0;
            const handler = (e) => {
                const now = Date.now();
                if (now - lastTrigger < 250) return;
                lastTrigger = now;
                if (e) {
                    e.stopPropagation();
                    if (e.cancelable && e.type === 'touchend') e.preventDefault();
                }
                callback(e);
            };
            el.addEventListener('click', handler);
            el.addEventListener('touchend', handler);
        };

        // Main Menu Toggle
        bindFab(this.btnMenu, () => this.toggleArcMenu());

        // Home Button (Synchronizes VideoFocusIndication)
        bindFab(this.btnHome, () => this.toggleVideoFocus());

        // Play / Pause Media Control
        bindFab(this.btnPlayPause, () => this.sendMediaKey(85));

        // Volume Popover Toggle & Actions
        bindFab(this.btnVolume, () => {
            if (this.volumePopover) this.volumePopover.classList.toggle('hidden');
        });
        bindFab(this.volMute, () => this.controlVolume('mute'));
        bindFab(this.volDown, () => this.controlVolume('down'));
        bindFab(this.volUp, () => this.controlVolume('up'));

        // Arc FAB Menu Actions
        bindFab(this.btnSettings, () => {
            this.closeArcMenu();
            this.openDrawer(this.drawerSettings);
            this.loadConfigSettings();
        });

        bindFab(this.btnBluetooth, () => {
            this.closeArcMenu();
            this.openDrawer(this.drawerBluetooth);
            this.loadBluetoothWidget();
        });

        bindFab(this.btnLogs, () => {
            this.closeArcMenu();
            this.openDrawer(this.drawerLogs);
            this.connectLogStream();
        });

        bindFab(this.btnWifi, () => {
            this.closeArcMenu();
            this.toggleWifiAp();
        });

        bindFab(this.btnFullscreen, () => {
            this.closeArcMenu();
            this.toggleFullscreen();
        });

        bindFab(this.btnClose, () => {
            this.closeArcMenu();
            this.closeWindow();
        });

        // Close Drawers
        if (this.btnCloseSettings) {
            this.btnCloseSettings.addEventListener('click', () => this.closeDrawer(this.drawerSettings));
        }
        if (this.btnCloseBluetooth) {
            this.btnCloseBluetooth.addEventListener('click', () => this.closeDrawer(this.drawerBluetooth));
        }
        if (this.btnCloseLogs) {
            this.btnCloseLogs.addEventListener('click', () => this.closeDrawer(this.drawerLogs));
        }
    }

    initModuleStreams() {
        this.currentStageIndex = 0;

        // Subscribe to channel_manager SSE status stream
        try {
            const chEvt = new EventSource('/api/channels/stream_status');
            chEvt.onmessage = (e) => {
                try {
                    const data = JSON.parse(e.data);
                    this.handleWorkflowStage(data);
                } catch (err) {}
            };
            chEvt.onerror = (err) => {
                if (this.currentStageIndex !== 0) {
                    this.handleWorkflowStage({ stage_index: 0 });
                }
            };
        } catch (e) {
            this.setStatus('offline');
        }

        // Subscribe to connectivity_manager SSE stream
        try {
            const connEvt = new EventSource('/api/connectivity/stream_status');
            connEvt.onmessage = (e) => {
                try {
                    const data = JSON.parse(e.data);
                    this.handleWorkflowStage(data);

                    const wifiBtn = document.getElementById('fab-wifi');
                    if (wifiBtn) {
                        const active = (data.wifi_ap && data.wifi_ap.active) || false;
                        if (active) {
                            wifiBtn.classList.add('active');
                        } else {
                            wifiBtn.classList.remove('active');
                        }
                    }
                } catch (err) {}
            };
            connEvt.onerror = (err) => {
                if (this.currentStageIndex !== 0) {
                    this.handleWorkflowStage({ stage_index: 0 });
                }
            };
        } catch (e) {}

        // Subscribe to media_server EventSource stream for real-time volume & mute updates
        try {
            const mediaEvt = new EventSource('/api/media/stream_status');
            mediaEvt.onmessage = (e) => {
                try {
                    const data = JSON.parse(e.data);
                    if (this.volLevelDisplay && data.volume !== undefined) {
                        this.volLevelDisplay.textContent = data.muted ? 'MUTE' : `${data.volume}%`;
                    }
                } catch (err) {}
            };
        } catch (e) {}
    }

    loadBluetoothWidget() {
        const container = document.getElementById('bluetooth-widget-container');
        if (!container) return;
        if (!this.bluetoothWidget) {
            this.bluetoothWidget = new BluetoothWidget(container);
        } else {
            this.bluetoothWidget.startStream();
        }
        this.bluetoothWidget.renderShell();
    }

    toggleArcMenu() {
        this.menuOpen = !this.menuOpen;
        if (this.menuOpen) {
            if (this.arcMenu) this.arcMenu.classList.add('open');
            if (this.btnMenu) this.btnMenu.classList.add('active');
        } else {
            this.closeArcMenu();
        }
    }

    closeArcMenu() {
        this.menuOpen = false;
        if (this.arcMenu) this.arcMenu.classList.remove('open');
        if (this.btnMenu) this.btnMenu.classList.remove('active');
    }

    openDrawer(drawer) {
        if (drawer) drawer.classList.add('open');
    }

    closeDrawer(drawer) {
        if (drawer) {
            drawer.classList.remove('open');
            if (drawer === this.drawerBluetooth && this.bluetoothWidget) {
                this.bluetoothWidget.stopStream();
            }
        }
    }

    closeWindow() {
        try {
            fetch('/api/system/close_window', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            }).catch(() => {});
        } catch (e) {}
        try {
            window.close();
        } catch (e) {}
    }

    toggleFullscreen() {
        if (!document.fullscreenElement) {
            document.documentElement.requestFullscreen().catch((err) => console.warn(err));
        } else {
            document.exitFullscreen().catch((err) => console.warn(err));
        }
    }

    toggleWifiAp() {
        fetch('/api/connectivity/wifi/start', { method: 'POST' })
            .then((res) => res.json())
            .then((data) => this.showToast('WiFi AP: ' + JSON.stringify(data), 'info'))
            .catch((err) => console.error('WiFi AP error:', err));
    }

    loadConfigSettings() {
        const container = document.getElementById('config-editor');
        if (!container) return;
        if (!this.settingsEngine) {
            this.settingsEngine = new SettingsWidgets(container);
        }
        this.settingsEngine.loadAndRender();
    }

    async connectLogStream() {
        if (!this.logSockets) this.logSockets = [];
        if (!this.availableModules) this.availableModules = {};

        const selectModule = document.getElementById('log-module-select');
        const selectLevel = document.getElementById('log-level-select');

        if (selectModule && !selectModule.dataset.bound) {
            selectModule.dataset.bound = 'true';
            selectModule.addEventListener('change', () => this.reconnectLogStream());
        }

        if (selectLevel && !selectLevel.dataset.bound) {
            selectLevel.dataset.bound = 'true';
            selectLevel.addEventListener('change', () => this.reconnectLogStream());
        }

        await this.fetchLogModules();
        this.reconnectLogStream();
    }

    async fetchLogModules() {
        try {
            const res = await fetch('/api/system/modules');
            const data = await res.json();
            const select = document.getElementById('log-module-select');
            if (!select || !data.modules) return;

            this.availableModules = data.modules;
            const currentVal = select.value;
            let html = `<option value="all">📋 All Modules</option>`;

            Object.values(data.modules).forEach((mod) => {
                const icon = mod.name.includes('connectivity') ? '📶' :
                             (mod.name.includes('tcp') ? '⚡' :
                             (mod.name.includes('channel') ? '🎥' :
                             (mod.name.includes('config') ? '⚙' : '🌐')));
                const url = mod.log_ws_url || `/api/${mod.name}/logs`;
                html += `<option value="${url}">${icon} ${mod.name}</option>`;
            });

            select.innerHTML = html;
            if (currentVal && select.querySelector(`option[value="${CSS.escape(currentVal)}"]`)) {
                select.value = currentVal;
            }
        } catch (err) {
            console.warn('Failed to fetch modules for log stream:', err);
        }
    }

    reconnectLogStream() {
        if (this.logSockets && this.logSockets.length > 0) {
            this.logSockets.forEach((ws) => {
                try { ws.close(); } catch (e) {}
            });
            this.logSockets = [];
        }

        const selectModule = document.getElementById('log-module-select');
        const selectLevel = document.getElementById('log-level-select');

        const selectedVal = selectModule ? selectModule.value : 'all';
        const level = selectLevel ? selectLevel.value : 'INFO';

        const host = window.location.host || '127.0.0.1:8000';
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';

        const urlsToConnect = [];

        if (selectedVal !== 'all') {
            urlsToConnect.push(selectedVal);
        } else {
            const modules = Object.values(this.availableModules);
            if (modules.length > 0) {
                modules.forEach((m) => {
                    if (m.log_ws_url) urlsToConnect.push(m.log_ws_url);
                });
            } else {
                urlsToConnect.push('/api/logs');
            }
        }

        if (this.logConsole) {
            this.logConsole.textContent = `Connecting to ${urlsToConnect.length} log stream(s) [level=${level}]...\n`;
        }

        urlsToConnect.forEach((relPath) => {
            const separator = relPath.includes('?') ? '&' : '?';
            const fullUrl = `${protocol}//${host}${relPath}${separator}level=${level}`;

            try {
                const ws = new WebSocket(fullUrl);
                ws.onmessage = (event) => {
                    if (this.logConsole) {
                        this.logConsole.textContent += event.data + '\n';
                        this.logConsole.scrollTop = this.logConsole.scrollHeight;
                    }
                };
                this.logSockets.push(ws);
            } catch (err) {
                console.error('Failed to open log WebSocket:', fullUrl, err);
            }
        });
    }

    toggleWifiAp() {
        fetch('/api/connectivity/wifi/start', { method: 'POST' })
            .then((res) => res.json())
            .then((data) => alert('WiFi AP Started: ' + JSON.stringify(data)))
            .catch((err) => console.error('WiFi AP error:', err));
    }

    triggerReconnect() {
        fetch('/api/tcp/restart', { method: 'POST' })
            .then(() => alert('Session restart requested.'))
            .catch((err) => console.error('Restart error:', err));
    }
}
