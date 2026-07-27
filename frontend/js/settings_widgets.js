/**
 * Touch-First Advanced Settings Suite for NemoHeadUnit
 * High-DPI in-car touchscreen friendly (48px+ tap targets, pill button selectors,
 * numeric touch steppers, and interactive channel accordion editor).
 */

export class SettingsWidgets {
    constructor(containerElement) {
        this.container = containerElement;
        this.configData = {};
        this.activeModule = null;
        this.formValues = {};
        this.viewMode = 'gui'; // 'gui' or 'raw'
        this.expandedChannels = new Set([1, 2, 3, 4]); // Default expanded channels
    }

    async loadAndRender() {
        this.container.innerHTML = '<div style="color: var(--text-secondary); padding: 24px; text-align: center; font-size: 16px;">Loading settings schemas...</div>';

        try {
            const res = await fetch('/api/config/all');
            this.configData = await res.json();
            this.render();
        } catch (err) {
            console.error('Failed to load settings:', err);
            this.container.innerHTML = `<div style="color: var(--danger-color); padding: 24px;">Error loading settings: ${err.message}</div>`;
        }
    }

    render() {
        const modules = Object.keys(this.configData);
        if (modules.length === 0) {
            this.container.innerHTML = '<div style="color: var(--text-secondary); padding: 24px; text-align: center;">No module configurations found.</div>';
            return;
        }

        if (!this.activeModule || !this.configData[this.activeModule]) {
            this.activeModule = modules[0];
        }

        let html = `
            <div class="settings-tabs touch-scroll">
                ${modules.map(mod => `
                    <button class="tab-btn touch-btn ${mod === this.activeModule ? 'active' : ''}" data-module="${mod}">
                        ${this.formatModuleName(mod)}
                    </button>
                `).join('')}
            </div>
            
            <div class="mode-switch-bar">
                <span class="mode-label">Configuration Mode:</span>
                <div class="pill-group">
                    <button class="pill-btn ${this.viewMode === 'gui' ? 'active' : ''}" id="btn-mode-gui">🎨 Touch Form</button>
                    <button class="pill-btn ${this.viewMode === 'raw' ? 'active' : ''}" id="btn-mode-raw">📄 Raw JSON</button>
                </div>
            </div>

            <div class="settings-form-container" id="module-form-container">
                ${this.viewMode === 'gui' ? this.renderModuleForm(this.activeModule) : this.renderRawJsonForm(this.activeModule)}
            </div>
            
            <div class="settings-actions">
                <button class="touch-save-btn" id="btn-save-settings">Save & Apply Settings</button>
            </div>
        `;

        this.container.innerHTML = html;
        this.bindEvents();
    }

    formatModuleName(mod) {
        const names = {
            connectivity_manager: '📶 Connectivity & AP',
            tcp_server: '⚡ TCP Server',
            channel_manager: '⚙ Channel Manager & SDR',
            proxy: '🌐 Gateway Proxy'
        };
        return names[mod] || mod;
    }

    renderModuleForm(moduleName) {
        const modData = this.configData[moduleName] || {};
        const config = modData.config || {};
        const schemaObj = modData.schema || {};

        if (!this.formValues[moduleName]) {
            this.formValues[moduleName] = JSON.parse(JSON.stringify(config));
        }

        const activeConfig = this.formValues[moduleName];

        if (moduleName === 'channel_manager') {
            return this.renderChannelManagerTouchForm(activeConfig, schemaObj);
        }

        const fields = Object.entries(schemaObj);
        if (fields.length === 0) {
            const configKeys = Object.keys(activeConfig);
            if (configKeys.length === 0) {
                return '<div style="color: var(--text-secondary); padding: 16px;">No parameters available for this module.</div>';
            }
            return configKeys.map(key => {
                const val = activeConfig[key];
                const inferredType = typeof val === 'boolean' ? 'bool' : (typeof val === 'number' ? 'int' : (typeof val === 'object' ? 'json' : 'string'));
                return this.renderTouchFormField(moduleName, { name: key, type: inferredType }, val);
            }).join('');
        }

        return fields.map(([fieldName, fieldSpec]) => {
            const spec = (typeof fieldSpec === 'object' && fieldSpec !== null) ? fieldSpec : {};
            return this.renderTouchFormField(moduleName, { name: fieldName, ...spec }, activeConfig[fieldName]);
        }).join('');
    }

    renderChannelManagerTouchForm(config, schemaObj) {
        const channels = config.channels || [];

        return `
            <!-- Card 1: Identity -->
            <div class="touch-card-section">
                <div class="touch-card-title">🚗 Vehicle & Head-Unit Identity</div>
                
                <div class="touch-field-group">
                    <label class="touch-label">Head Unit Name</label>
                    <input type="text" class="touch-input" data-module="channel_manager" data-field="head_unit_name" value="${config.head_unit_name || ''}">
                </div>

                <div class="touch-field-group">
                    <label class="touch-label">Car Model & Year</label>
                    <div style="display: flex; gap: 8px;">
                        <input type="text" class="touch-input" style="flex: 2;" data-module="channel_manager" data-field="car_model" value="${config.car_model || ''}">
                        <input type="text" class="touch-input" style="flex: 1;" data-module="channel_manager" data-field="car_year" value="${config.car_year || ''}">
                    </div>
                </div>

                <div class="touch-field-group">
                    <label class="touch-label">Driver Position</label>
                    <div class="pill-group" data-module="channel_manager" data-field="driver_position">
                        <button class="pill-btn ${config.driver_position === 'LEFT' ? 'active' : ''}" data-value="LEFT">LEFT (LHD)</button>
                        <button class="pill-btn ${config.driver_position === 'RIGHT' ? 'active' : ''}" data-value="RIGHT">RIGHT (RHD)</button>
                    </div>
                </div>
            </div>

            <!-- Card 2: Interactive Channel Accordion Editor -->
            <div class="touch-card-section">
                <div class="touch-card-header-bar">
                    <div class="touch-card-title" style="margin-bottom: 0;">📡 Active Protocol Channel Descriptors (${channels.length})</div>
                </div>

                <div class="accordion-list">
                    ${channels.map((ch, idx) => this.renderChannelAccordionItem(ch, idx)).join('')}
                </div>

                <div style="margin-top: 14px; display: flex; gap: 10px;">
                    <button class="touch-add-btn" id="btn-add-channel">➕ Add New Channel Descriptor</button>
                </div>
            </div>
        `;
    }

    renderChannelAccordionItem(ch, index) {
        const chId = ch.channel_id;
        const isExpanded = this.expandedChannels.has(chId);
        const typeName = this.getChannelTypeName(ch);

        return `
            <div class="accordion-card ${isExpanded ? 'expanded' : ''}" data-channel-id="${chId}">
                <div class="accordion-header" data-toggle-ch="${chId}">
                    <span class="accordion-arrow">${isExpanded ? '▼' : '▶'}</span>
                    <span class="accordion-title">Channel ${chId}: <strong style="color: var(--accent-color);">${typeName}</strong></span>
                    <button class="touch-delete-btn" data-delete-idx="${index}" title="Remove Channel">&times;</button>
                </div>
                
                ${isExpanded ? `
                    <div class="accordion-body">
                        ${this.renderChannelDetailsForm(ch, index)}
                    </div>
                ` : ''}
            </div>
        `;
    }

    getChannelTypeName(ch) {
        if (ch.input_channel) return 'Touch Screen Input (ch 1)';
        if (ch.sensor_channel) return 'Sensors (ch 2)';
        if (ch.av_channel) {
            const codec = ch.av_channel.codec || '';
            const audioType = ch.av_channel.audio_type || '';
            if (codec.includes('VIDEO')) return `H.264 Video (ch ${ch.channel_id})`;
            return `Audio Stream [${audioType || 'MEDIA'}] (ch ${ch.channel_id})`;
        }
        if (ch.av_input_channel) return `Microphone AV Input (ch ${ch.channel_id})`;
        if (ch.bluetooth_channel) return `Bluetooth (ch ${ch.channel_id})`;
        if (ch.wifi_channel) return `Wi-Fi (ch ${ch.channel_id})`;
        return `Channel ${ch.channel_id}`;
    }

    renderChannelDetailsForm(ch, index) {
        if (ch.av_channel && (ch.av_channel.codec || '').includes('VIDEO')) {
            const vcfg = ((ch.av_channel.video_configs || [])[0]) || {};
            const res = vcfg.video_resolution || 'VIDEO_1280x720';
            const fps = vcfg.video_fps || '_30';
            const dpi = vcfg.dpi !== undefined ? vcfg.dpi : 140;

            return `
                <div class="touch-field-group">
                    <label class="touch-label">Resolution Preset</label>
                    <div class="pill-group" data-ch-index="${index}" data-ch-field="res">
                        <button class="pill-btn ${res === 'VIDEO_1280x720' ? 'active' : ''}" data-val="VIDEO_1280x720">1280x720 (720p)</button>
                        <button class="pill-btn ${res === 'VIDEO_1920x1080' ? 'active' : ''}" data-val="VIDEO_1920x1080">1920x1080 (1080p)</button>
                        <button class="pill-btn ${res === 'VIDEO_800x480' ? 'active' : ''}" data-val="VIDEO_800x480">800x480 (480p)</button>
                    </div>
                </div>

                <div class="touch-field-group">
                    <label class="touch-label">Frame Rate & Display DPI</label>
                    <div style="display: flex; gap: 12px; align-items: center;">
                        <div class="pill-group" data-ch-index="${index}" data-ch-field="fps">
                            <button class="pill-btn ${fps === '_30' ? 'active' : ''}" data-val="_30">30 FPS</button>
                            <button class="pill-btn ${fps === '_60' ? 'active' : ''}" data-val="_60">60 FPS</button>
                        </div>

                        <div class="stepper-widget" data-ch-index="${index}" data-ch-field="dpi">
                            <button class="stepper-btn" data-step="-10">-</button>
                            <span class="stepper-val">${dpi} DPI</span>
                            <button class="stepper-btn" data-step="10">+</button>
                        </div>
                    </div>
                </div>
            `;
        }

        return `<pre class="json-preview">${JSON.stringify(ch, null, 2)}</pre>`;
    }

    renderRawJsonForm(moduleName) {
        const config = this.formValues[moduleName] || (this.configData[moduleName] || {}).config || {};
        const jsonStr = JSON.stringify(config, null, 2);

        return `
            <div class="touch-field-group">
                <label class="touch-label">Raw JSON Editor</label>
                <textarea class="json-textarea touch-textarea" id="raw-json-editor" rows="18">${jsonStr}</textarea>
            </div>
        `;
    }

    renderTouchFormField(moduleName, field, currentValue) {
        const fieldName = field.name;
        const fieldType = field.type || 'string';
        const label = field.description || fieldName.replace(/_/g, ' ').toUpperCase();
        const value = (currentValue !== undefined) ? currentValue : field.default;

        if (fieldType === 'bool') {
            return `
                <div class="touch-field-group flex-between">
                    <label class="touch-label">${label}</label>
                    <label class="toggle-switch">
                        <input type="checkbox" data-module="${moduleName}" data-field="${fieldName}" ${value ? 'checked' : ''}>
                        <span class="slider"></span>
                    </label>
                </div>
            `;
        }

        if (fieldType === 'enum' && field.choices) {
            return `
                <div class="touch-field-group">
                    <label class="touch-label">${label}</label>
                    <div class="pill-group" data-module="${moduleName}" data-field="${fieldName}">
                        ${field.choices.map(c => `
                            <button class="pill-btn ${c === value ? 'active' : ''}" data-value="${c}">${c}</button>
                        `).join('')}
                    </div>
                </div>
            `;
        }

        if (fieldType === 'int' || fieldType === 'float') {
            const step = fieldType === 'float' ? 0.1 : 1;
            return `
                <div class="touch-field-group flex-between">
                    <label class="touch-label">${label}</label>
                    <div class="stepper-widget" data-module="${moduleName}" data-field="${fieldName}">
                        <button class="stepper-btn" data-step="-1">-</button>
                        <span class="stepper-val">${value !== undefined ? value : 0}</span>
                        <button class="stepper-btn" data-step="1">+</button>
                    </div>
                </div>
            `;
        }

        if (typeof value === 'object' && value !== null) {
            return `
                <div class="touch-field-group">
                    <label class="touch-label">${label} (JSON)</label>
                    <textarea class="json-textarea touch-textarea" data-module="${moduleName}" data-field="${fieldName}" rows="4">${JSON.stringify(value, null, 2)}</textarea>
                </div>
            `;
        }

        return `
            <div class="touch-field-group">
                <label class="touch-label">${label}</label>
                <input type="text" class="touch-input" data-module="${moduleName}" data-field="${fieldName}" value="${value !== undefined ? value : ''}">
            </div>
        `;
    }

    bindEvents() {
        // Tab switching
        this.container.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                this.activeModule = e.target.getAttribute('data-module');
                this.render();
            });
        });

        // View mode toggle
        const btnGui = this.container.querySelector('#btn-mode-gui');
        const btnRaw = this.container.querySelector('#btn-mode-raw');

        if (btnGui) btnGui.addEventListener('click', () => { this.viewMode = 'gui'; this.render(); });
        if (btnRaw) btnRaw.addEventListener('click', () => { this.viewMode = 'raw'; this.render(); });

        // Accordion expand / collapse
        this.container.querySelectorAll('[data-toggle-ch]').forEach(el => {
            el.addEventListener('click', (e) => {
                if (e.target.classList.contains('touch-delete-btn')) return;
                const chId = Number(el.getAttribute('data-toggle-ch'));
                if (this.expandedChannels.has(chId)) {
                    this.expandedChannels.delete(chId);
                } else {
                    this.expandedChannels.add(chId);
                }
                this.render();
            });
        });

        // Delete channel
        this.container.querySelectorAll('[data-delete-idx]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const idx = Number(btn.getAttribute('data-delete-idx'));
                if (this.formValues.channel_manager && this.formValues.channel_manager.channels) {
                    this.formValues.channel_manager.channels.splice(idx, 1);
                    this.render();
                }
            });
        });

        // Add channel button
        const addBtn = this.container.querySelector('#btn-add-channel');
        if (addBtn) {
            addBtn.addEventListener('click', () => {
                if (!this.formValues.channel_manager) return;
                const channels = this.formValues.channel_manager.channels || [];
                const nextId = (channels.reduce((max, c) => Math.max(max, c.channel_id || 0), 0)) + 1;
                channels.push({
                    channel_id: nextId,
                    sensor_channel: { sensors: [{ type: "NIGHT_DATA" }] }
                });
                this.expandedChannels.add(nextId);
                this.render();
            });
        }

        // Pill group selectors
        this.container.querySelectorAll('.pill-group').forEach(group => {
            const mod = group.getAttribute('data-module');
            const field = group.getAttribute('data-field');

            group.querySelectorAll('.pill-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    group.querySelectorAll('.pill-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');

                    const val = btn.getAttribute('data-value');
                    if (mod && field && this.formValues[mod]) {
                        this.formValues[mod][field] = val;
                    }
                });
            });
        });

        // Video preset pills
        this.container.querySelectorAll('[data-ch-index]').forEach(group => {
            const idx = Number(group.getAttribute('data-ch-index'));
            const field = group.getAttribute('data-ch-field');
            const channels = (this.formValues.channel_manager || {}).channels || [];
            const videoCh = channels[idx];

            if (videoCh && videoCh.av_channel && videoCh.av_channel.video_configs) {
                const vcfg = videoCh.av_channel.video_configs[0];
                group.querySelectorAll('.pill-btn').forEach(btn => {
                    btn.addEventListener('click', () => {
                        group.querySelectorAll('.pill-btn').forEach(b => b.classList.remove('active'));
                        btn.classList.add('active');
                        const val = btn.getAttribute('data-val');
                        if (field === 'res') vcfg.video_resolution = val;
                        if (field === 'fps') vcfg.video_fps = val;
                    });
                });
            }
        });

        // Stepper buttons
        this.container.querySelectorAll('.stepper-widget').forEach(widget => {
            const mod = widget.getAttribute('data-module');
            const field = widget.getAttribute('data-field');
            const chIdx = widget.getAttribute('data-ch-index');
            const chField = widget.getAttribute('data-ch-field');
            const valSpan = widget.querySelector('.stepper-val');

            widget.querySelectorAll('.stepper-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const step = Number(btn.getAttribute('data-step'));
                    if (mod && field && this.formValues[mod]) {
                        let cur = Number(this.formValues[mod][field] || 0);
                        cur += step;
                        this.formValues[mod][field] = cur;
                        if (valSpan) valSpan.textContent = cur;
                    } else if (chIdx !== null && chField === 'dpi') {
                        const channels = (this.formValues.channel_manager || {}).channels || [];
                        const vcfg = ((channels[chIdx] || {}).av_channel || {}).video_configs[0];
                        if (vcfg) {
                            let cur = Number(vcfg.dpi || 140) + step;
                            vcfg.dpi = cur;
                            if (valSpan) valSpan.textContent = `${cur} DPI`;
                        }
                    }
                });
            });
        });

        // Input text changes
        this.container.querySelectorAll('input.touch-input').forEach(input => {
            input.addEventListener('change', (e) => {
                const mod = e.target.getAttribute('data-module');
                const field = e.target.getAttribute('data-field');
                if (mod && field && this.formValues[mod]) {
                    this.formValues[mod][field] = e.target.value;
                }
            });
        });

        // Save button
        const saveBtn = this.container.querySelector('#btn-save-settings');
        if (saveBtn) {
            saveBtn.addEventListener('click', () => this.saveActiveModuleSettings(saveBtn));
        }
    }

    async saveActiveModuleSettings(buttonEl) {
        if (!this.activeModule) return;

        buttonEl.disabled = true;
        buttonEl.textContent = 'Saving...';

        const rawTextarea = this.container.querySelector('#raw-json-editor');
        if (rawTextarea) {
            try {
                this.formValues[this.activeModule] = JSON.parse(rawTextarea.value);
            } catch (err) {
                alert('Cannot save: Invalid raw JSON format!');
                buttonEl.disabled = false;
                buttonEl.textContent = 'Save & Apply Settings';
                return;
            }
        }

        const updatedConfig = this.formValues[this.activeModule] || {};

        try {
            const res = await fetch(`/api/config/${this.activeModule}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updatedConfig)
            });

            await res.json();
            buttonEl.textContent = 'Saved Successfully!';
            buttonEl.style.background = '#00e676';

            setTimeout(() => {
                buttonEl.disabled = false;
                buttonEl.textContent = 'Save & Apply Settings';
                buttonEl.style.background = '';
            }, 2000);
        } catch (err) {
            console.error('Save settings error:', err);
            buttonEl.disabled = false;
            buttonEl.textContent = 'Error Saving Settings!';
            buttonEl.style.background = '#ff5252';
        }
    }
}
