/**
 * Bluetooth Connection Manager Widget
 * Handles Bluetooth discovery scans, listing available/paired devices,
 * initiating pairing requests, PIN validation, and device connection management.
 */

export class BluetoothWidget {
    constructor(containerEl) {
        this.container = containerEl;
        this.isScanning = false;
        this.devices = [];
        this.activeDevice = null;
        this.pairingPendingDevice = null;
        this.scanInterval = null;
    }

    renderShell() {
        this.container.innerHTML = `
            <div class="bt-widget-container">
                <div class="bt-header-actions">
                    <div class="bt-status-badge" id="bt-status-indicator">
                        <span class="bt-dot"></span>
                        <span id="bt-status-text">Bluetooth Ready</span>
                    </div>
                    <button class="bt-scan-btn" id="bt-scan-btn">
                        <span class="bt-scan-icon">🔍</span>
                        <span id="bt-scan-label">Scan Devices</span>
                    </button>
                </div>

                <div class="bt-section-title">PAIRED & KNOWN DEVICES</div>
                <div class="bt-device-list" id="bt-paired-list">
                    <div class="bt-empty-state">Loading paired devices...</div>
                </div>

                <div class="bt-section-title">DISCOVERED DEVICES</div>
                <div class="bt-device-list" id="bt-discovered-list">
                    <div class="bt-empty-state">Tap 'Scan Devices' to discover nearby phones</div>
                </div>

                <!-- Pairing Modal / Dialog Container -->
                <div class="bt-modal-overlay hidden" id="bt-pairing-modal">
                    <div class="bt-modal-card">
                        <div class="bt-modal-title">Pairing Request</div>
                        <div class="bt-modal-subtitle" id="bt-modal-device-name">Phone Name</div>
                        <div class="bt-pin-display" id="bt-modal-pin">------</div>
                        <p class="bt-modal-text">Confirm that this PIN matches the code displayed on your phone.</p>
                        <div class="bt-modal-actions">
                            <button class="bt-modal-btn reject" id="bt-modal-reject">Reject</button>
                            <button class="bt-modal-btn accept" id="bt-modal-accept">Confirm & Pair</button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        this.bindEvents();
        this.refreshStatus();
        this.refreshPairedDevices();
    }

    bindEvents() {
        const scanBtn = this.container.querySelector('#bt-scan-btn');
        if (scanBtn) {
            scanBtn.addEventListener('click', () => this.toggleScan());
        }

        const btnAccept = this.container.querySelector('#bt-modal-accept');
        const btnReject = this.container.querySelector('#bt-modal-reject');

        if (btnAccept) {
            btnAccept.addEventListener('click', () => this.confirmPairing(true));
        }
        if (btnReject) {
            btnReject.addEventListener('click', () => this.confirmPairing(false));
        }
    }

    async refreshStatus() {
        try {
            const res = await fetch('/api/connectivity/status');
            if (!res.ok) return;
            const data = await res.json();
            
            const dot = this.container.querySelector('.bt-dot');
            const statusText = this.container.querySelector('#bt-status-text');
            const scanBtn = this.container.querySelector('#bt-scan-btn');
            const scanLabel = this.container.querySelector('#bt-scan-label');

            if (data.discovering) {
                this.isScanning = true;
                if (statusText) statusText.textContent = 'Scanning...';
                if (dot) dot.className = 'bt-dot scanning';
                if (scanBtn) scanBtn.classList.add('scanning');
                if (scanLabel) scanLabel.textContent = 'Stop Scan';
            } else {
                this.isScanning = false;
                if (statusText) {
                    statusText.textContent = data.rfcomm_connected ? 'Connected (AA Active)' : 'Bluetooth Ready';
                }
                if (dot) dot.className = data.rfcomm_connected ? 'bt-dot connected' : 'bt-dot';
                if (scanBtn) scanBtn.classList.remove('scanning');
                if (scanLabel) scanLabel.textContent = 'Scan Devices';
            }
        } catch (err) {
            console.warn('Failed to fetch Bluetooth status:', err);
        }
    }

    async refreshPairedDevices() {
        try {
            const res = await fetch('/api/connectivity/paired');
            if (!res.ok) return;
            const data = await res.json();
            
            const container = this.container.querySelector('#bt-paired-list');
            if (!container) return;

            const devices = data.devices || [];
            if (devices.length === 0) {
                container.innerHTML = `<div class="bt-empty-state">No paired Bluetooth devices</div>`;
                return;
            }

            let html = '';
            devices.forEach((dev) => {
                const isConnected = dev.connected;
                html += `
                    <div class="bt-device-card ${isConnected ? 'connected' : ''}">
                        <div class="bt-device-info">
                            <span class="bt-device-icon">${isConnected ? '📱' : '📲'}</span>
                            <div>
                                <div class="bt-device-name">${this.escapeHtml(dev.name || dev.address)}</div>
                                <div class="bt-device-mac">${dev.address}</div>
                            </div>
                        </div>
                        <div class="bt-device-actions">
                            ${isConnected 
                                ? `<button class="bt-action-btn disconnect" data-addr="${dev.address}">Disconnect</button>`
                                : `<button class="bt-action-btn connect" data-addr="${dev.address}">Connect</button>`
                            }
                            <button class="bt-action-btn remove" data-addr="${dev.address}" title="Forget Device">🗑️</button>
                        </div>
                    </div>
                `;
            });

            container.innerHTML = html;
            this.bindDeviceCardEvents(container);
        } catch (err) {
            console.warn('Failed to fetch paired devices:', err);
        }
    }

    bindDeviceCardEvents(parentEl) {
        parentEl.querySelectorAll('.bt-action-btn.connect').forEach((btn) => {
            btn.addEventListener('click', (e) => this.connectDevice(e.target.dataset.addr));
        });

        parentEl.querySelectorAll('.bt-action-btn.disconnect').forEach((btn) => {
            btn.addEventListener('click', (e) => this.disconnectDevice(e.target.dataset.addr));
        });

        parentEl.querySelectorAll('.bt-action-btn.remove').forEach((btn) => {
            btn.addEventListener('click', (e) => this.removeDevice(e.target.dataset.addr));
        });
    }

    async toggleScan() {
        if (this.isScanning) {
            this.isScanning = false;
            this.refreshStatus();
            return;
        }

        try {
            this.isScanning = true;
            this.refreshStatus();

            const discoveredList = this.container.querySelector('#bt-discovered-list');
            if (discoveredList) {
                discoveredList.innerHTML = `<div class="bt-empty-state"><div class="status-spinner inline"></div> Searching for Bluetooth devices...</div>`;
            }

            const res = await fetch('/api/connectivity/discover', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ duration_sec: 12 })
            });

            if (res.ok) {
                // Poll for discovered devices during scan duration
                let elapsed = 0;
                const pollInterval = setInterval(async () => {
                    elapsed += 2;
                    await this.refreshPairedDevices();
                    if (elapsed >= 12 || !this.isScanning) {
                        clearInterval(pollInterval);
                        this.isScanning = false;
                        this.refreshStatus();
                    }
                }, 2000);
            }
        } catch (err) {
            console.error('Scan request error:', err);
            this.isScanning = false;
            this.refreshStatus();
        }
    }

    async pairDevice(address, name) {
        try {
            this.pairingPendingDevice = { address, name };
            const modal = this.container.querySelector('#bt-pairing-modal');
            const nameEl = this.container.querySelector('#bt-modal-device-name');
            const pinEl = this.container.querySelector('#bt-modal-pin');

            if (nameEl) nameEl.textContent = name || address;
            if (pinEl) pinEl.textContent = 'Initiating...';
            if (modal) modal.classList.remove('hidden');

            const res = await fetch('/api/connectivity/pair', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device_address: address })
            });

            const data = await res.json();
            if (!res.ok) {
                alert(`Pairing failed: ${data.message || 'Error'}`);
                if (modal) modal.classList.add('hidden');
            }
        } catch (err) {
            console.error('Pairing call error:', err);
        }
    }

    async confirmPairing(accept) {
        if (!this.pairingPendingDevice) return;
        const endpoint = accept ? '/api/connectivity/pair/confirm' : '/api/connectivity/pair/reject';
        
        try {
            await fetch(endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device_address: this.pairingPendingDevice.address })
            });
        } catch (err) {
            console.error('Confirm pairing error:', err);
        } finally {
            const modal = this.container.querySelector('#bt-pairing-modal');
            if (modal) modal.classList.add('hidden');
            this.pairingPendingDevice = null;
            this.refreshPairedDevices();
        }
    }

    async connectDevice(address) {
        try {
            const res = await fetch('/api/connectivity/connect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device_address: address })
            });
            const data = await res.json();
            if (!res.ok) alert(`Connection error: ${data.message}`);
            this.refreshPairedDevices();
            this.refreshStatus();
        } catch (err) {
            console.error('Connect call failed:', err);
        }
    }

    async disconnectDevice(address) {
        try {
            await fetch('/api/connectivity/disconnect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device_address: address })
            });
            this.refreshPairedDevices();
            this.refreshStatus();
        } catch (err) {
            console.error('Disconnect call failed:', err);
        }
    }

    async removeDevice(address) {
        if (!confirm(`Forget Bluetooth device ${address}?`)) return;
        try {
            await fetch('/api/connectivity/remove', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ device_address: address })
            });
            this.refreshPairedDevices();
        } catch (err) {
            console.error('Remove paired device call failed:', err);
        }
    }

    escapeHtml(str) {
        return String(str).replace(/[&<>"']/g, (m) => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[m]));
    }
}
