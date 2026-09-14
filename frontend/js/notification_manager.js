/**
 * notification_manager.js — Notification Toasts and Dashboard Feed Manager for Web GUI.
 */

export class NotificationManager {
    constructor() {
        this.bannerContainer = document.getElementById('notification-banner-container');
        this.cardEl = document.getElementById('dashboard-notification-card');
        this.listEl = document.getElementById('dash-notif-list');
        this.btnClear = document.getElementById('btn-clear-notifications');

        this.notifications = [];
        this._bindEvents();
    }

    _bindEvents() {
        if (this.btnClear) {
            this.btnClear.addEventListener('click', () => this.clearAll());
        }
    }

    addNotification(data) {
        if (!data) return;
        const { id, title = 'Alert', text = '', app_name = 'Android Auto' } = data;

        this.notifications.unshift(data);
        if (this.notifications.length > 10) this.notifications.pop();

        this._renderCard();
        this._showToast(data);
    }

    removeNotification(id) {
        this.notifications = this.notifications.filter(n => n.id !== id);
        this._renderCard();
    }

    clearAll() {
        this.notifications = [];
        this._renderCard();
    }

    _showToast(data) {
        if (!this.bannerContainer) return;

        const toast = document.createElement('div');
        toast.className = 'notif-toast-banner';
        toast.innerHTML = `
            <div class="notif-toast-icon">🔔</div>
            <div class="notif-toast-content">
                <div class="notif-toast-title">${this._escapeHtml(data.app_name || 'Alert')} • ${this._escapeHtml(data.title)}</div>
                <div class="notif-toast-body">${this._escapeHtml(data.text || '')}</div>
            </div>
            <button class="notif-toast-close">&times;</button>
        `;

        const closeBtn = toast.querySelector('.notif-toast-close');
        closeBtn.addEventListener('click', () => {
            toast.remove();
            this.sendAction(data.id, 'dismiss');
        });

        this.bannerContainer.appendChild(toast);

        // Auto remove after 6 seconds
        setTimeout(() => {
            if (toast.parentElement) {
                toast.classList.add('fade-out');
                setTimeout(() => toast.remove(), 400);
            }
        }, 6000);
    }

    _renderCard() {
        if (!this.cardEl || !this.listEl) return;

        if (this.notifications.length === 0) {
            this.cardEl.classList.add('hidden');
            this.listEl.innerHTML = '';
            return;
        }

        this.cardEl.classList.remove('hidden');
        this.listEl.innerHTML = this.notifications.map(n => `
            <div class="notif-feed-item">
                <div class="notif-item-title">🔔 [${this._escapeHtml(n.app_name || 'Alert')}] ${this._escapeHtml(n.title)}</div>
                <div class="notif-item-text">${this._escapeHtml(n.text || '')}</div>
            </div>
        `).join('');
    }

    async sendAction(id, actionId = 'dismiss') {
        try {
            await fetch('/api/channels/notification/action', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id, action_id: actionId }),
            });
        } catch (err) {
            console.error('Failed to send notification action:', err);
        }
    }

    _escapeHtml(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
}
