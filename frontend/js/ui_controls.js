/**
 * UI Controls Manager: Arc Radial FAB Menu, Workflow Status Polling, Settings & Logs
 */

import { SettingsWidgets } from './settings_widgets.js';
import { BluetoothWidget } from './bluetooth_widget.js';
import { DiagnosticWidget } from './diagnostic.js';

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
        this.btnDiagnostic = document.getElementById('fab-diagnostic');
        this.btnFullscreen = document.getElementById('fab-fullscreen');

        // Drawers
        this.drawerSettings = document.getElementById('settings-drawer');
        this.drawerBluetooth = document.getElementById('bluetooth-drawer');
        this.drawerLogs = document.getElementById('logs-drawer');
        this.drawerDiagnostic = document.getElementById('diagnostics-drawer');
        this.btnCloseSettings = document.getElementById('close-settings');
        this.btnCloseBluetooth = document.getElementById('close-bluetooth');
        this.btnCloseLogs = document.getElementById('close-logs');
        this.btnCloseDiagnostic = document.getElementById('close-diagnostics');

        // Screen & State
        this.disconnectedScreen = document.getElementById('disconnected-screen');
        this.logConsole = document.getElementById('log-console');
        this.bluetoothWidget = null;
        this.diagnosticWidget = new DiagnosticWidget('diagnostics-drawer-content');
        this.isVideoFocused = true;
        this.isPlaying = false;

        this.initEventListeners();
        this.initModuleStreams();
        this.initMediaBrowser();
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
            this._updateClockAuxInfo(null, null);
            return;
        }

        // Update live media & navigation info on disconnected/home screen
        this._updateClockAuxInfo(data.media, data.navigation);

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

    _updateClockAuxInfo(media, nav) {
        const grid = document.getElementById('dashboard-grid');
        const navCard = document.getElementById('dashboard-nav-card');
        const mediaCard = document.getElementById('dashboard-media-card');

        let hasNav = false;
        let hasMedia = false;

        // Cache persistent state
        if (media !== undefined && media !== null && Object.keys(media).length > 0) {
            this._lastMedia = Object.assign(this._lastMedia || {}, media);
        } else if (media === null) {
            this._lastMedia = null;
        }
        const effectiveMedia = this._lastMedia;

        if (nav !== undefined && nav !== null && (nav.road || nav.distance_meters >= 0)) {
            this._lastNav = Object.assign(this._lastNav || {}, nav);
        } else if (nav === null || (nav && nav.distance_meters === -1 && !nav.road)) {
            this._lastNav = null;
        }
        const effectiveNav = this._lastNav;

        // 1. Update Navigation Card
        if (navCard) {
            const hasActiveNav = effectiveNav && (effectiveNav.road || (effectiveNav.distance_meters !== undefined && effectiveNav.distance_meters >= 0));
            if (hasActiveNav) {
                hasNav = true;
                navCard.classList.remove('hidden');

                const distEl = document.getElementById('dash-nav-distance');
                const roadEl = document.getElementById('dash-nav-road');
                const etaEl = document.getElementById('dash-nav-eta');
                const iconEl = document.getElementById('dash-nav-icon');

                if (distEl) {
                    if (effectiveNav.distance_meters !== undefined && effectiveNav.distance_meters >= 0) {
                        distEl.textContent = effectiveNav.distance_meters >= 1000
                            ? `${(effectiveNav.distance_meters / 1000).toFixed(1)} km`
                            : `${Math.round(effectiveNav.distance_meters)} m`;
                    } else {
                        distEl.textContent = '—';
                    }
                }

                if (roadEl) {
                    roadEl.textContent = effectiveNav.road || 'Follow Route';
                }

                if (etaEl) {
                    if (effectiveNav.eta_seconds && effectiveNav.eta_seconds > 0) {
                        const mins = Math.floor(effectiveNav.eta_seconds / 60);
                        const hrs = Math.floor(mins / 60);
                        etaEl.textContent = hrs > 0 ? `ETA: ${hrs}h ${mins % 60}m` : `ETA: ${mins} min`;
                    } else {
                        etaEl.textContent = '';
                    }
                }

                // Render Icon / Turn Direction
                if (iconEl) {
                    if (effectiveNav.turn_icon) {
                        iconEl.innerHTML = `<img src="${effectiveNav.turn_icon}" style="max-width: 64px; max-height: 64px; object-fit: contain;">`;
                    } else {
                        // Vector Arrow default
                        const side = effectiveNav.turn_side || 0;
                        if (side === 1) { // Left
                            iconEl.innerHTML = '<svg viewBox="0 0 24 24" width="48" height="48" fill="#00e676"><path d="M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z"/></svg>';
                        } else if (side === 2) { // Right
                            iconEl.innerHTML = '<svg viewBox="0 0 24 24" width="48" height="48" fill="#00e676"><path d="M12 4l-1.41 1.41L16.17 11H4v2h12.17l-5.58 5.59L12 20l8-8z"/></svg>';
                        } else { // Straight
                            iconEl.innerHTML = '<svg viewBox="0 0 24 24" width="48" height="48" fill="#00e676"><path d="M12 2L4.5 20.29l.71.71L12 18l6.79 3 .71-.71z"/></svg>';
                        }
                    }
                }
            } else {
                navCard.classList.add('hidden');
            }
        }

        // 2. Update Media Card
        if (mediaCard) {
            const hasActiveMedia = effectiveMedia && (effectiveMedia.title || effectiveMedia.artist);
            if (hasActiveMedia) {
                hasMedia = true;
                mediaCard.classList.remove('hidden');

                const titleEl = document.getElementById('dash-media-title');
                const artistEl = document.getElementById('dash-media-artist');
                const albumEl = document.getElementById('dash-media-album');
                const badgeEl = document.getElementById('dash-media-badge');
                const artEl = document.getElementById('dash-media-art');

                if (titleEl) titleEl.textContent = effectiveMedia.title || 'Unknown Track';
                if (artistEl) artistEl.textContent = effectiveMedia.artist || '—';
                if (albumEl) albumEl.textContent = effectiveMedia.album || '';

                if (badgeEl) {
                    const st = effectiveMedia.playback_state;
                    badgeEl.textContent = st === 2 ? 'NOW PLAYING' : (st === 3 ? 'PAUSED' : 'MEDIA');
                    badgeEl.style.color = st === 2 ? '#3fb950' : (st === 3 ? '#d29922' : '#58a6ff');
                }

                if (artEl) {
                    if (effectiveMedia.album_art) {
                        artEl.style.backgroundImage = `url("${effectiveMedia.album_art}")`;
                        artEl.innerHTML = '';
                    } else {
                        artEl.style.backgroundImage = 'none';
                        artEl.innerHTML = '<span class="default-art-icon">🎵</span>';
                    }
                }
            } else {
                mediaCard.classList.add('hidden');
            }
        }

        // 3. Grid Mode Class Switcher
        if (grid) {
            grid.classList.remove('mode-clock-only', 'mode-clock-media', 'mode-nav-grid');
            if (hasNav) {
                grid.classList.add('mode-nav-grid');
            } else if (hasMedia) {
                grid.classList.add('mode-clock-media');
            } else {
                grid.classList.add('mode-clock-only');
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

    initMediaBrowser() {
        const btnToggle = document.getElementById('btn-web-toggle-browse');
        const viewNowPlaying = document.getElementById('media-view-nowplaying');
        const viewBrowser = document.getElementById('media-view-browser');
        const btnBack = document.getElementById('btn-browser-back');
        const pathDisplay = document.getElementById('browser-path-display');
        const listContainer = document.getElementById('browser-items-list');

        let currentPath = '/';
        const pathHistory = [];

        const fetchPath = async (path) => {
            currentPath = path;
            if (pathDisplay) pathDisplay.textContent = path;
            try {
                const res = await fetch(`/api/channels/media/browse?path=${encodeURIComponent(path)}`);
                const data = await res.json();
                renderItems(data.items || []);
            } catch (err) {
                console.error('Failed to fetch media browse:', err);
            }
        };

        const renderItems = (items) => {
            if (!listContainer) return;
            listContainer.innerHTML = '';
            items.forEach(item => {
                const row = document.createElement('div');
                row.className = 'browser-item-row';
                const isFolder = item.type === 'folder';
                row.innerHTML = `
                    <span class="item-icon">${isFolder ? '📁' : '▶'}</span>
                    <div class="item-text">
                        <div class="item-title">${item.title}</div>
                        ${item.subtitle ? `<div class="item-sub">${item.subtitle}</div>` : ''}
                    </div>
                `;
                row.addEventListener('click', async () => {
                    if (isFolder) {
                        pathHistory.push(currentPath);
                        await fetchPath(item.id);
                    } else {
                        await fetch('/api/channels/media/play_item', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ path: item.id }),
                        });
                    }
                });
                listContainer.appendChild(row);
            });
        };

        if (btnToggle) {
            btnToggle.addEventListener('click', () => {
                const isBrowser = viewBrowser && !viewBrowser.classList.contains('hidden');
                if (isBrowser) {
                    viewBrowser.classList.add('hidden');
                    viewNowPlaying.classList.remove('hidden');
                    btnToggle.textContent = '📁 Browse';
                } else {
                    viewBrowser.classList.remove('hidden');
                    viewNowPlaying.classList.add('hidden');
                    btnToggle.textContent = '🎵 Now Playing';
                    fetchPath(currentPath);
                }
            });
        }

        if (btnBack) {
            btnBack.addEventListener('click', () => {
                if (pathHistory.length > 0) {
                    const prev = pathHistory.pop();
                    fetchPath(prev);
                }
            });
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

        bindFab(this.btnDiagnostic, () => {
            this.closeArcMenu();
            this.openDrawer(this.drawerDiagnostic);
            this.diagnosticWidget.open();
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
        if (this.btnCloseDiagnostic) {
            this.btnCloseDiagnostic.addEventListener('click', () => {
                this.closeDrawer(this.drawerDiagnostic);
                this.diagnosticWidget.close();
            });
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
