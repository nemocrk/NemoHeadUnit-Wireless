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
    this.startStream();
    this.refreshAll();
  }

  startStream() {
    this.stopStream();
    try {
      console.log(
        "📡 [BluetoothWidget] Initializing EventSource stream: /api/connectivity/stream_status",
      );
      this.evtSource = new EventSource("/api/connectivity/stream_status");

      this.evtSource.onopen = () => {
        console.log(
          "✅ [BluetoothWidget] EventSource stream connected cleanly — stopping fallback polling",
        );
        this.stopPolling();
      };

      this.evtSource.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);

          // Update header status text & dot
          const dot = this.container.querySelector(".bt-dot");
          const statusText = this.container.querySelector("#bt-status-text");
          const scanBtn = this.container.querySelector("#bt-scan-btn");
          const scanLabel = this.container.querySelector("#bt-scan-label");

          if (data.discovering) {
            this.isScanning = true;
            if (statusText) statusText.textContent = "Scanning...";
            if (dot) dot.className = "bt-dot scanning";
            if (scanBtn) scanBtn.classList.add("scanning");
            if (scanLabel) scanLabel.textContent = "Stop Scan";
          } else {
            this.isScanning = false;
            if (statusText) {
              statusText.textContent = data.rfcomm_connected
                ? "Connected (AA Active)"
                : "Bluetooth Ready";
            }
            if (dot)
              dot.className = data.rfcomm_connected
                ? "bt-dot connected"
                : "bt-dot";
            if (scanBtn) scanBtn.classList.remove("scanning");
            if (scanLabel) scanLabel.textContent = "Scan Devices";
          }

          // Render paired and discovered lists directly from streamed payload
          if (data.paired_devices) {
            this.renderPairedDevices(data.paired_devices);
          }
          if (data.discovered_devices) {
            this.renderDiscoveredDevices(
              data.discovered_devices.filter(
                (x) =>
                  !data.paired_devices
                    .map((y) => y.address)
                    .includes(x.address),
              ),
            );
          }
          if (data.pairing_pin && data.pairing_device) {
            this.showPairingModal(data.pairing_device, data.pairing_pin);
          }
        } catch (err) {
          console.warn("[BluetoothWidget] SSE JSON parse warning:", err);
        }
      };

      this.evtSource.onerror = (err) => {
        if (!this.evtSource) return;
        if (this.evtSource.readyState === EventSource.CLOSED) {
          console.warn(
            "⚠️ [BluetoothWidget] EventSource closed — switching to fallback polling (4s)",
          );
          if (!this.pollTimer) {
            this.startPolling(4000);
          }
        }
      };
    } catch (e) {
      console.error(
        "❌ [BluetoothWidget] Failed to initialize EventSource:",
        e,
      );
      this.startPolling(4000);
    }
  }

  stopStream() {
    if (this.evtSource) {
      this.evtSource.close();
      this.evtSource = null;
    }
    this.stopPolling();
  }

  startPolling(intervalMs = 3000) {
    this.stopPolling();
    this.pollTimer = setInterval(() => {
      if (!this.isScanning) {
        this.refreshAll();
      }
    }, intervalMs);
  }

  stopPolling() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  bindEvents() {
    const scanBtn = this.container.querySelector("#bt-scan-btn");
    if (scanBtn) {
      scanBtn.addEventListener("click", () => this.toggleScan());
    }

    const btnAccept = this.container.querySelector("#bt-modal-accept");
    const btnReject = this.container.querySelector("#bt-modal-reject");

    if (btnAccept) {
      btnAccept.addEventListener("click", () => this.confirmPairing(true));
    }
    if (btnReject) {
      btnReject.addEventListener("click", () => this.confirmPairing(false));
    }
  }

  async refreshStatus() {
    try {
      const res = await fetch("/api/connectivity/status");
      const contentType = res.headers.get("content-type") || "";
      if (!res.ok || !contentType.includes("application/json")) return;
      const data = await res.json();

      const dot = this.container.querySelector(".bt-dot");
      const statusText = this.container.querySelector("#bt-status-text");
      const scanBtn = this.container.querySelector("#bt-scan-btn");
      const scanLabel = this.container.querySelector("#bt-scan-label");

      if (data.discovering) {
        this.isScanning = true;
        if (statusText) statusText.textContent = "Scanning...";
        if (dot) dot.className = "bt-dot scanning";
        if (scanBtn) scanBtn.classList.add("scanning");
        if (scanLabel) scanLabel.textContent = "Stop Scan";
        // Periodically fetch and render intermediate discovered devices while scanning
        this.refreshDiscoveredDevices();
      } else {
        this.isScanning = false;
        if (statusText) {
          statusText.textContent = data.rfcomm_connected
            ? "Connected (AA Active)"
            : "Bluetooth Ready";
        }
        if (dot)
          dot.className = data.rfcomm_connected ? "bt-dot connected" : "bt-dot";
        if (scanBtn) scanBtn.classList.remove("scanning");
        if (scanLabel) scanLabel.textContent = "Scan Devices";
      }

      // Auto-trigger or update PIN modal if incoming/outgoing pairing request has a PIN
      if (data.pairing_pin && data.pairing_device) {
        const currentName =
          this.pairingPendingDevice &&
          this.pairingPendingDevice.address === data.pairing_device
            ? this.pairingPendingDevice.name
            : data.pairing_device;
        this.showPairingModal(
          data.pairing_device,
          data.pairing_pin,
          currentName,
        );
      }
    } catch (err) {
      console.warn("Failed to fetch Bluetooth status:", err);
    }
  }

  showPairingModal(address, pin, name) {
    this.pairingPendingDevice = { address, name: name || address };
    const modal = this.container.querySelector("#bt-pairing-modal");
    const nameEl = this.container.querySelector("#bt-modal-device-name");
    const pinEl = this.container.querySelector("#bt-modal-pin");

    if (nameEl) nameEl.textContent = name || address;
    if (pinEl) pinEl.textContent = pin || "------";
    if (modal) modal.classList.remove("hidden");
  }

  async pairDevice(address, name) {
    try {
      this.showPairingModal(address, "Initiating...", name);

      const res = await fetch("/api/connectivity/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_address: address }),
      });

      const data = await res.json();
      if (res.ok) {
        if (data.pin) {
          const pinEl = this.container.querySelector("#bt-modal-pin");
          if (pinEl) pinEl.textContent = data.pin;
        }
      } else {
        alert(`Pairing failed: ${data.message || "Error"}`);
        const modal = this.container.querySelector("#bt-pairing-modal");
        if (modal) modal.classList.add("hidden");
        this.pairingPendingDevice = null;
      }
    } catch (err) {
      console.error("Pairing call error:", err);
    }
  }

  async refreshPairedDevices() {
    try {
      const res = await fetch("/api/connectivity/paired");
      const contentType = res.headers.get("content-type") || "";
      if (!res.ok || !contentType.includes("application/json")) return;
      const data = await res.json();
      this.pairedDevices = data.devices || [];
      this.renderPairedDevices(this.pairedDevices);
    } catch (err) {
      console.warn("Failed to fetch paired devices:", err);
    }
  }

  renderPairedDevices(devicesList = null) {
    const container = this.container.querySelector("#bt-paired-list");
    if (!container) return;

    const devices = devicesList || this.pairedDevices || [];
    if (devices.length === 0) {
      container.innerHTML = `<div class="bt-empty-state">No paired Bluetooth devices</div>`;
      return;
    }

    let html = "";
    devices.forEach((dev) => {
      const isConnected = dev.connected;
      html += `
                    <div class="bt-device-card ${isConnected ? "connected" : ""}">
                        <div class="bt-device-info">
                            <span class="bt-device-icon">${isConnected ? "📱" : "📲"}</span>
                            <div>
                                <div class="bt-device-name">${this.escapeHtml(dev.name || dev.address)}</div>
                                <div class="bt-device-mac">${dev.address}</div>
                            </div>
                        </div>
                        <div class="bt-device-actions">
                            ${
                              isConnected
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
  }

  bindDeviceCardEvents(parentEl) {
    parentEl.querySelectorAll(".bt-action-btn.connect").forEach((btn) => {
      btn.addEventListener("click", (e) =>
        this.connectDevice(e.target.dataset.addr),
      );
    });

    parentEl.querySelectorAll(".bt-action-btn.disconnect").forEach((btn) => {
      btn.addEventListener("click", (e) =>
        this.disconnectDevice(e.target.dataset.addr),
      );
    });

    parentEl.querySelectorAll(".bt-action-btn.remove").forEach((btn) => {
      btn.addEventListener("click", (e) =>
        this.removeDevice(e.target.dataset.addr),
      );
    });
  }

  renderDiscoveredDevices(devices = []) {
    const container = this.container.querySelector("#bt-discovered-list");
    if (!container) return;

    if (devices.length === 0) {
      container.innerHTML = `<div class="bt-empty-state">No devices found. Tap 'Scan Devices' to retry.</div>`;
      return;
    }

    let html = "";
    devices.forEach((dev) => {
      html += `
                <div class="bt-device-card">
                    <div class="bt-device-info">
                        <span class="bt-device-icon">📲</span>
                        <div>
                            <div class="bt-device-name">${this.escapeHtml(dev.name || dev.address)}</div>
                            <div class="bt-device-mac">${dev.address}</div>
                        </div>
                    </div>
                    <div class="bt-device-actions">
                        <button class="bt-action-btn connect" data-addr="${dev.address}" data-name="${this.escapeHtml(dev.name || "")}">Pair</button>
                    </div>
                </div>
            `;
    });

    container.innerHTML = html;
    container.querySelectorAll(".bt-action-btn.connect").forEach((btn) => {
      btn.addEventListener("click", (e) =>
        this.pairDevice(e.target.dataset.addr, e.target.dataset.name),
      );
    });
  }

  async refreshDiscoveredDevices() {
    try {
      const res = await fetch("/api/connectivity/discovered");
      const contentType = res.headers.get("content-type") || "";
      if (!res.ok || !contentType.includes("application/json")) return;
      const data = await res.json();
      this.renderDiscoveredDevices(data.devices || []);
    } catch (err) {
      console.warn("Failed to fetch discovered devices:", err);
    }
  }

  async refreshAll() {
    if (this.evtSource && this.evtSource.readyState === EventSource.OPEN) {
      console.log(
        "ℹ️ [BluetoothWidget] Stream is OPEN & active — skipping redundant refreshAll() HTTP polling",
      );
      return;
    }
    try {
      await Promise.all([
        this.refreshStatus(),
        this.refreshPairedDevices(),
        this.refreshDiscoveredDevices(),
      ]);
    } catch (err) {
      console.warn("Error refreshing Bluetooth widget:", err);
    }
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

      const discoveredList = this.container.querySelector(
        "#bt-discovered-list",
      );
      if (discoveredList) {
        discoveredList.innerHTML = `<div class="bt-empty-state"><div class="status-spinner inline"></div> Searching for Bluetooth devices...</div>`;
      }

      const res = await fetch("/api/connectivity/discover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ duration_sec: 12 }),
      });

      if (res.ok) {
        const data = await res.json();
        this.renderDiscoveredDevices(data.devices || []);
      }
    } catch (err) {
      console.error("Scan request error:", err);
    } finally {
      this.isScanning = false;
      await this.refreshAll();
    }
  }

  async confirmPairing(accept) {
    if (!this.pairingPendingDevice) return;
    const endpoint = accept
      ? "/api/connectivity/pair/confirm"
      : "/api/connectivity/pair/reject";

    try {
      await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          device_address: this.pairingPendingDevice.address,
        }),
      });
    } catch (err) {
      console.error("Confirm pairing error:", err);
    } finally {
      const modal = this.container.querySelector("#bt-pairing-modal");
      if (modal) modal.classList.add("hidden");
      this.pairingPendingDevice = null;
      await this.refreshAll();
    }
  }

  async connectDevice(address) {
    try {
      const res = await fetch("/api/connectivity/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_address: address }),
      });
      const data = await res.json();
      if (!res.ok) alert(`Connection error: ${data.message}`);
    } catch (err) {
      console.error("Connect call failed:", err);
    } finally {
      await this.refreshAll();
    }
  }

  async disconnectDevice(address) {
    try {
      await fetch("/api/connectivity/disconnect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_address: address }),
      });
    } catch (err) {
      console.error("Disconnect call failed:", err);
    } finally {
      await this.refreshAll();
    }
  }

  async removeDevice(address) {
    if (!confirm(`Forget Bluetooth device ${address}?`)) return;
    try {
      await fetch("/api/connectivity/paired/remove", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_address: address }),
      });
    } catch (err) {
      console.error("Remove paired device call failed:", err);
    } finally {
      await this.refreshAll();
    }
  }

  escapeHtml(str) {
    return String(str).replace(
      /[&<>"']/g,
      (m) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[m],
    );
  }
}
