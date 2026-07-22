/**
 * Schema-Driven Interactive Settings Widgets for NemoHeadUnit ConfigManager
 */

export class SettingsWidgets {
    constructor(containerElement) {
        this.container = containerElement;
        this.configData = {};
        this.activeModule = null;
        this.formValues = {};
    }

    async loadAndRender() {
        this.container.innerHTML = '<div style="color: var(--text-secondary); padding: 20px;">Loading settings schemas...</div>';

        try {
            const res = await fetch('/api/config/all');
            this.configData = await res.json();
            this.render();
        } catch (err) {
            console.error('Failed to load settings:', err);
            this.container.innerHTML = `<div style="color: var(--danger-color); padding: 20px;">Error loading settings: ${err.message}</div>`;
        }
    }

    render() {
        const modules = Object.keys(this.configData);
        if (modules.length === 0) {
            this.container.innerHTML = '<div style="color: var(--text-secondary); padding: 20px;">No module configurations found.</div>';
            return;
        }

        if (!this.activeModule || !this.configData[this.activeModule]) {
            this.activeModule = modules[0];
        }

        let html = `
            <div class="settings-tabs">
                ${modules.map(mod => `
                    <button class="tab-btn ${mod === this.activeModule ? 'active' : ''}" data-module="${mod}">
                        ${this.formatModuleName(mod)}
                    </button>
                `).join('')}
            </div>
            <div class="settings-form-container" id="module-form-container">
                ${this.renderModuleForm(this.activeModule)}
            </div>
            <div class="settings-actions">
                <button class="save-btn" id="btn-save-settings">Save & Apply Settings</button>
            </div>
        `;

        this.container.innerHTML = html;
        this.bindEvents();
    }

    formatModuleName(mod) {
        const names = {
            connectivity_manager: '📶 Connectivity & AP',
            tcp_server: '⚡ TCP Server',
            channel_manager: '⚙ Channel Manager',
            proxy: '🌐 Gateway Proxy'
        };
        return names[mod] || mod;
    }

    renderModuleForm(moduleName) {
        const modData = this.configData[moduleName] || {};
        const config = modData.config || {};
        const schemaObj = modData.schema || {};

        this.formValues[moduleName] = { ...config };

        const fields = Object.entries(schemaObj);
        if (fields.length === 0) {
            const configKeys = Object.keys(config);
            if (configKeys.length === 0) {
                return '<div style="color: var(--text-secondary); padding: 12px;">No parameters available for this module.</div>';
            }
            return configKeys.map(key => {
                const val = config[key];
                const inferredType = typeof val === 'boolean' ? 'bool' : (typeof val === 'number' ? 'int' : 'string');
                return this.renderFormField(moduleName, { name: key, type: inferredType }, val);
            }).join('');
        }

        return fields.map(([fieldName, fieldSpec]) => {
            const spec = (typeof fieldSpec === 'object' && fieldSpec !== null) ? fieldSpec : {};
            return this.renderFormField(moduleName, { name: fieldName, ...spec }, config[fieldName]);
        }).join('');
    }

    renderFormField(moduleName, field, currentValue) {
        const fieldName = field.name;
        const fieldType = field.type || 'string';
        const label = field.description || fieldName.replace(/_/g, ' ').toUpperCase();
        const value = (currentValue !== undefined) ? currentValue : field.default;

        let inputWidget = '';

        if (fieldType === 'bool') {
            inputWidget = `
                <label class="toggle-switch">
                    <input type="checkbox" data-module="${moduleName}" data-field="${fieldName}" ${value ? 'checked' : ''}>
                    <span class="slider"></span>
                </label>
            `;
        } else if (fieldType === 'enum' && field.choices) {
            inputWidget = `
                <select class="styled-select" data-module="${moduleName}" data-field="${fieldName}">
                    ${field.choices.map(choice => `
                        <option value="${choice}" ${choice === value ? 'selected' : ''}>${choice}</option>
                    `).join('')}
                </select>
            `;
        } else if (fieldType === 'int' || fieldType === 'float') {
            const min = field.min !== undefined ? field.min : 0;
            const max = field.max !== undefined ? field.max : 100;
            const step = fieldType === 'float' ? 0.1 : 1;

            inputWidget = `
                <div class="range-widget">
                    <input type="range" class="styled-range" data-module="${moduleName}" data-field="${fieldName}"
                        min="${min}" max="${max}" step="${step}" value="${value}"
                        oninput="this.nextElementSibling.value = this.value">
                    <output class="range-val">${value}</output>
                </div>
            `;
        } else {
            inputWidget = `
                <input type="text" class="styled-input" data-module="${moduleName}" data-field="${fieldName}" value="${value || ''}">
            `;
        }

        return `
            <div class="form-group">
                <div class="form-label">${label}</div>
                <div class="form-widget">${inputWidget}</div>
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

        // Input changes
        this.container.querySelectorAll('input, select').forEach(input => {
            input.addEventListener('change', (e) => {
                const mod = e.target.getAttribute('data-module');
                const field = e.target.getAttribute('data-field');
                let val = e.target.value;

                if (e.target.type === 'checkbox') {
                    val = e.target.checked;
                } else if (e.target.type === 'range' || e.target.type === 'number') {
                    val = Number(val);
                }

                if (!this.formValues[mod]) this.formValues[mod] = {};
                this.formValues[mod][field] = val;
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

        const updatedConfig = this.formValues[this.activeModule] || {};

        try {
            const res = await fetch(`/api/config/${this.activeModule}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updatedConfig)
            });

            const result = await res.json();
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
