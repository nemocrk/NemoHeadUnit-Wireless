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
        this.btnAudio = document.getElementById('btn-audio');
        this.btnMenu = document.getElementById('btn-menu');
        this.btnReconnect = document.getElementById('fab-reconnect');
        this.btnClose = document.getElementById('btn-close');

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

        // Workflow Status Card
        this.disconnectedScreen = document.getElementById('disconnected-screen');
        this.workflowText = document.getElementById('workflow-text');
        this.logConsole = document.getElementById('log-console');

        this.bluetoothWidget = null;

        this.initEventListeners();
        this.startWorkflowPolling();
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

        // Home Button (Show Disconnected Screen / Clock)
        bindFab(this.btnHome, () => this.disconnectedScreen.classList.toggle('hidden'));

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

        bindFab(this.btnReconnect, () => {
            this.closeArcMenu();
            this.triggerReconnect();
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

    loadBluetoothWidget() {
        const container = document.getElementById('bluetooth-widget-container');
        if (!container) return;
        if (!this.bluetoothWidget) {
            this.bluetoothWidget = new BluetoothWidget(container);
        }
        this.bluetoothWidget.renderShell();
    }

    toggleArcMenu() {
        this.menuOpen = !this.menuOpen;
        if (this.menuOpen) {
            this.arcMenu.classList.add('open');
            this.btnMenu.classList.add('active');
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
        if (drawer) drawer.classList.remove('open');
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

    startWorkflowPolling() {
        const safeFetchJson = async (url) => {
            try {
                const res = await fetch(url);
                if (!res.ok) return null;
                return await res.json();
            } catch (e) {
                return null;
            }
        };

        const updateWorkflow = async () => {
            if (!this.workflowText) return;
            try {
                const [connRes, tcpRes, chRes] = await Promise.all([
                    safeFetchJson('/api/connectivity/status'),
                    safeFetchJson('/api/tcp/status'),
                    safeFetchJson('/api/channels/status')
                ]);

                if (tcpRes && tcpRes.client_address) {
                    if (tcpRes.tls_active) {
                        const activeCount = (chRes && chRes.active_channels) ? chRes.active_channels.length : 0;
                        if (activeCount > 0) {
                            this.workflowText.textContent = `Android Auto Session Connected — Streaming Video (${activeCount} active channels)`;
                        } else {
                            this.workflowText.textContent = `TLS Secured with ${tcpRes.client_address} — Executing Handshake...`;
                        }
                    } else {
                        this.workflowText.textContent = `Phone Connected (${tcpRes.client_address}) — Initializing TLS...`;
                    }
                } else if (connRes && connRes.wifi_ap && connRes.wifi_ap.active) {
                    this.workflowText.textContent = `WiFi Hotspot Active: ${connRes.wifi_ap.ssid || 'NemoTestAP'} — Awaiting Phone Connection...`;
                } else if (connRes && connRes.bluetooth && connRes.bluetooth.discovering) {
                    this.workflowText.textContent = 'Scanning Bluetooth for Phone Pair...';
                } else {
                    this.workflowText.textContent = 'Initializing NemoHeadUnit Adapters...';
                }
            } catch (err) {
                this.workflowText.textContent = 'Awaiting NemoHeadUnit Backend Service...';
            }
        };

        updateWorkflow();
        setInterval(updateWorkflow, 2000);
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
