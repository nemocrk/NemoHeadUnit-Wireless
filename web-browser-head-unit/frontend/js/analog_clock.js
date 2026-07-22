/**
 * Analog Clock Renderer for Default Disconnected Screen
 */

export class AnalogClock {
    constructor(canvasElement, dateElement) {
        this.canvas = canvasElement;
        this.ctx = canvasElement.getContext('2d');
        this.dateEl = dateElement;
        this.animationFrame = null;

        this.resize();
        this.start();
    }

    resize() {
        const size = this.canvas.parentElement.clientWidth || 260;
        this.canvas.width = size;
        this.canvas.height = size;
        this.radius = size / 2;
    }

    start() {
        const draw = () => {
            this.drawClock();
            this.animationFrame = requestAnimationFrame(draw);
        };
        draw();
    }

    stop() {
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
        }
    }

    drawClock() {
        const ctx = this.ctx;
        const radius = this.radius;
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

        // Center origin
        ctx.save();
        ctx.translate(radius, radius);

        // Clock Face Background Glow
        const grad = ctx.createRadialGradient(0, 0, radius * 0.8, 0, 0, radius);
        grad.addColorStop(0, '#0f172a');
        grad.addColorStop(1, '#020617');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(0, 0, radius - 5, 0, 2 * Math.PI);
        ctx.fill();

        // Outer Ring
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.1)';
        ctx.lineWidth = 4;
        ctx.stroke();

        // Hour Ticks
        for (let i = 0; i < 12; i++) {
            const angle = (i * Math.PI) / 6;
            ctx.rotate(angle);
            ctx.beginPath();
            ctx.moveTo(0, -radius + 15);
            ctx.lineTo(0, -radius + 28);
            ctx.strokeStyle = i % 3 === 0 ? '#00e676' : 'rgba(255, 255, 255, 0.3)';
            ctx.lineWidth = i % 3 === 0 ? 4 : 2;
            ctx.stroke();
            ctx.rotate(-angle);
        }

        // Current Time
        const now = new Date();
        const hour = now.getHours() % 12;
        const minute = now.getMinutes();
        const second = now.getSeconds() + now.getMilliseconds() / 1000;

        // Hour Hand
        const hourAngle = ((hour + minute / 60) * Math.PI) / 6;
        this.drawHand(ctx, hourAngle, radius * 0.5, 6, '#f8fafc');

        // Minute Hand
        const minuteAngle = ((minute + second / 60) * Math.PI) / 30;
        this.drawHand(ctx, minuteAngle, radius * 0.72, 4, '#94a3b8');

        // Second Hand (Sweeping Neon Accent)
        const secondAngle = (second * Math.PI) / 30;
        this.drawHand(ctx, secondAngle, radius * 0.85, 2, '#00e676');

        // Center Pin
        ctx.fillStyle = '#00e676';
        ctx.beginPath();
        ctx.arc(0, 0, 6, 0, 2 * Math.PI);
        ctx.fill();

        ctx.restore();

        // Update Date Display
        if (this.dateEl) {
            const options = { weekday: 'short', month: 'short', day: 'numeric' };
            this.dateEl.textContent = now.toLocaleDateString(undefined, options).toUpperCase();
        }
    }

    drawHand(ctx, angle, length, width, color) {
        ctx.save();
        ctx.rotate(angle);
        ctx.beginPath();
        ctx.moveTo(0, 10);
        ctx.lineTo(0, -length);
        ctx.strokeStyle = color;
        ctx.lineWidth = width;
        ctx.lineCap = 'round';
        ctx.stroke();
        ctx.restore();
    }
}
