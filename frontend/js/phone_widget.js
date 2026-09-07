/**
 * phone_widget.js — Phone Status & In-Call Widget for Web Dashboard.
 * Handles active call UI, timer, battery/signal indicator updates, and call action triggers.
 */

export class PhoneWidget {
    constructor() {
        this.cardEl = document.getElementById('dashboard-phone-card');
        this.stateEl = document.getElementById('dash-phone-state');
        this.durationEl = document.getElementById('dash-phone-duration');
        this.nameEl = document.getElementById('dash-caller-name');
        this.numberEl = document.getElementById('dash-caller-number');
        this.btnAnswer = document.getElementById('btn-web-call-answer');
        this.btnHangup = document.getElementById('btn-web-call-hangup');

        this.cmdSignal = document.getElementById('cmd-signal');
        this.cmdBattery = document.getElementById('cmd-battery');
        this.batteryText = document.getElementById('battery-text');

        // Command Bar In-Call Pill
        this.cmdInCallPill = document.getElementById('cmd-in-call-pill');
        this.cmdCallerName = document.getElementById('cmd-caller-name');
        this.cmdCallTimer = document.getElementById('cmd-call-timer');
        this.btnCmdAnswer = document.getElementById('btn-cmd-answer');
        this.btnCmdMute = document.getElementById('btn-cmd-mute');
        this.btnCmdHangup = document.getElementById('btn-cmd-hangup');
        this.iconCmdMute = document.getElementById('icon-cmd-mute');

        this.isInCall = false;
        this.isMicMuted = false;
        this.durationSeconds = 0;
        this.timerInterval = null;
        this._battery = -1;
        this._signal = -1;

        this._bindEvents();
    }

    _bindEvents() {
        if (this.btnAnswer) {
            this.btnAnswer.addEventListener('click', () => this.sendAction('answer'));
        }
        if (this.btnHangup) {
            this.btnHangup.addEventListener('click', () => this.sendAction('hangup'));
        }
        if (this.btnCmdAnswer) {
            this.btnCmdAnswer.addEventListener('click', () => this.sendAction('answer'));
        }
        if (this.btnCmdHangup) {
            this.btnCmdHangup.addEventListener('click', () => this.sendAction('hangup'));
        }
        if (this.btnCmdMute) {
            this.btnCmdMute.addEventListener('click', () => this._toggleMicMute());
        }
    }

    _toggleMicMute() {
        this.isMicMuted = !this.isMicMuted;
        if (this.btnCmdMute) {
            this.btnCmdMute.classList.toggle('muted', this.isMicMuted);
            this.btnCmdMute.title = this.isMicMuted ? 'Unmute Microphone' : 'Mute Microphone';
        }
        this.sendAction('mute');
    }

    updatePhoneStatus(data) {
        if (!data) return;

        const {
            is_in_call = false,
            call_state = 'IDLE',
            caller_name = '',
            caller_number = '',
            call_duration_seconds = 0,
            is_charging = false,
            is_connected = null,
        } = data;

        const sig = data.signal_strength !== undefined ? data.signal_strength : data.signal_bars;
        const bat = data.battery_level !== undefined ? data.battery_level : data.battery_pct;

        if (is_connected === false) {
            this._signal = -1;
            this._battery = -1;
        } else {
            if (sig !== null && sig !== undefined && sig >= 0) {
                this._signal = sig;
            }
            if (bat !== null && bat !== undefined && bat >= 0) {
                this._battery = bat;
            }
        }

        // Update Top/Bottom Status Indicators
        if (this.cmdSignal) {
            if (this._signal >= 0) {
                this.cmdSignal.title = `Cellular Signal: ${this._signal}/5`;
            } else {
                this.cmdSignal.title = 'Cellular Signal: Unknown';
            }
        }
        if (this.cmdBattery && this.batteryText) {
            if (this._battery >= 0) {
                this.batteryText.textContent = `${this._battery}%`;
                this.cmdBattery.title = `Phone Battery: ${this._battery}% ${is_charging ? '(Charging)' : ''}`;
            } else {
                this.batteryText.textContent = '--%';
                this.cmdBattery.title = 'Phone Battery: Unknown';
            }
        }

        // Update Call Card
        this.isInCall = is_in_call;
        this.durationSeconds = call_duration_seconds;

        if (this.nameEl) this.nameEl.textContent = caller_name || caller_number || 'Unknown Caller';
        if (this.numberEl) this.numberEl.textContent = caller_number || '';

        this._renderDuration();

        if (this.stateEl) {
            if (call_state === 'RINGING') {
                this.stateEl.textContent = 'Incoming Call...';
                this.stateEl.style.color = '#58a6ff';
                if (this.btnAnswer) this.btnAnswer.style.display = 'inline-block';
                if (this.btnHangup) this.btnHangup.textContent = 'Decline';
            } else if (['ACTIVE', 'CONNECTING', 'DIALING'].includes(call_state)) {
                this.stateEl.textContent = 'In Call';
                this.stateEl.style.color = '#3fb950';
                if (this.btnAnswer) this.btnAnswer.style.display = 'none';
                if (this.btnHangup) this.btnHangup.textContent = 'End Call';
            } else if (call_state === 'HOLD') {
                this.stateEl.textContent = 'Call on Hold';
                this.stateEl.style.color = '#d29922';
                if (this.btnAnswer) this.btnAnswer.style.display = 'none';
                if (this.btnHangup) this.btnHangup.textContent = 'End Call';
            } else {
                this.stateEl.textContent = 'Call Ended';
                this.stateEl.style.color = '#8b949e';
                if (this.btnAnswer) this.btnAnswer.style.display = 'none';
                if (this.btnHangup) this.btnHangup.textContent = 'Close';
            }
        }

        if (this.cardEl) {
            if (this.isInCall) {
                this.cardEl.classList.remove('hidden');
                this._startTimer();
            } else {
                this.cardEl.classList.add('hidden');
                this._stopTimer();
            }
        }

        // Update Command Bar In-Call Pill
        if (this.cmdInCallPill) {
            if (this.isInCall && !['IDLE', 'DISCONNECTED'].includes(call_state)) {
                this.cmdInCallPill.classList.remove('hidden');
                if (this.cmdCallerName) {
                    this.cmdCallerName.textContent = caller_name || caller_number || 'In Call';
                }
                if (call_state === 'RINGING') {
                    if (this.cmdCallTimer) this.cmdCallTimer.textContent = 'Incoming...';
                    if (this.btnCmdAnswer) this.btnCmdAnswer.style.display = 'flex';
                    if (this.btnCmdMute) this.btnCmdMute.style.display = 'none';
                    if (this.btnCmdHangup) this.btnCmdHangup.title = 'Decline Call';
                } else {
                    if (this.btnCmdAnswer) this.btnCmdAnswer.style.display = 'none';
                    if (this.btnCmdMute) this.btnCmdMute.style.display = 'flex';
                    if (this.btnCmdHangup) this.btnCmdHangup.title = 'End Call';
                }
            } else {
                this.cmdInCallPill.classList.add('hidden');
            }
        }
    }

    _startTimer() {
        if (this.timerInterval) return;
        this.timerInterval = setInterval(() => {
            this.durationSeconds++;
            this._renderDuration();
        }, 1000);
    }

    _stopTimer() {
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
            this.timerInterval = null;
        }
    }

    _renderDuration() {
        const mins = Math.floor(this.durationSeconds / 60).toString().padStart(2, '0');
        const secs = (this.durationSeconds % 60).toString().padStart(2, '0');
        const formatted = `${mins}:${secs}`;
        if (this.durationEl) {
            this.durationEl.textContent = formatted;
        }
        if (this.cmdCallTimer && this.isInCall && this.cmdCallTimer.textContent !== 'Incoming...') {
            this.cmdCallTimer.textContent = formatted;
        }
    }

    async sendAction(action) {
        try {
            await fetch('/api/channels/phone/action', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action }),
            });
        } catch (err) {
            console.error('Failed to send phone action:', err);
        }
    }
}
