/**
 * Multi-Touch & Gesture Handler for Android Auto Touchscreen Input
 */

export class TouchHandler {
    constructor(canvasElement, apiEndpoint = '/api/channels/input/touch') {
        this.canvas = canvasElement;
        this.apiEndpoint = apiEndpoint;
        
        this.initialPinchDistance = 0;
        this.isPinching = false;
        
        this.bindEvents();
    }

    bindEvents() {
        const c = this.canvas;

        // Pointer / Touch Events
        c.addEventListener('pointerdown', (e) => this.handlePointerDown(e));
        c.addEventListener('pointermove', (e) => this.handlePointerMove(e));
        c.addEventListener('pointerup', (e) => this.handlePointerUp(e));
        c.addEventListener('pointercancel', (e) => this.handlePointerUp(e));

        // Touch Multi-Touch Pinch-to-Zoom
        c.addEventListener('touchstart', (e) => this.handleTouchStart(e));
        c.addEventListener('touchmove', (e) => this.handleTouchMove(e));
        c.addEventListener('touchend', (e) => this.handleTouchEnd(e));
    }

    getNormalizedCoords(clientX, clientY) {
        const rect = this.canvas.getBoundingClientRect();
        const scaleX = (this.canvas.width || 1280) / rect.width;
        const scaleY = (this.canvas.height || 720) / rect.height;

        const x = Math.round((clientX - rect.left) * scaleX);
        const y = Math.round((clientY - rect.top) * scaleY);

        return {
            x: Math.max(0, Math.min(x, 1280)),
            y: Math.max(0, Math.min(y, 720))
        };
    }

    sendTouchEvent(x, y, action) {
        // action: 0 = DOWN, 1 = MOVE, 2 = UP
        fetch(this.apiEndpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ x, y, action })
        }).catch((err) => console.debug('Touch send error:', err));
    }

    handlePointerDown(e) {
        if (e.target !== this.canvas) return;
        if (e.pointerType === 'touch' && !e.isPrimary) return;
        const { x, y } = this.getNormalizedCoords(e.clientX, e.clientY);
        this.sendTouchEvent(x, y, 0); // DOWN
    }

    handlePointerMove(e) {
        if (e.target !== this.canvas || e.buttons !== 1) return;
        const { x, y } = this.getNormalizedCoords(e.clientX, e.clientY);
        this.sendTouchEvent(x, y, 1); // MOVE
    }

    handlePointerUp(e) {
        if (e.target !== this.canvas) return;
        const { x, y } = this.getNormalizedCoords(e.clientX, e.clientY);
        this.sendTouchEvent(x, y, 2); // UP
    }

    handleTouchStart(e) {
        if (e.touches.length === 2) {
            this.isPinching = true;
            this.initialPinchDistance = Math.hypot(
                e.touches[0].clientX - e.touches[1].clientX,
                e.touches[0].clientY - e.touches[1].clientY
            );
        }
    }

    handleTouchMove(e) {
        if (this.isPinching && e.touches.length === 2) {
            const currentDistance = Math.hypot(
                e.touches[0].clientX - e.touches[1].clientX,
                e.touches[0].clientY - e.touches[1].clientY
            );

            const scaleDelta = currentDistance - this.initialPinchDistance;
            if (Math.abs(scaleDelta) > 20) {
                const centerClientX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
                const centerClientY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
                const { x, y } = this.getNormalizedCoords(centerClientX, centerClientY);

                // Send pinch/zoom action indicator
                const actionType = scaleDelta > 0 ? 3 : 4; // 3 = ZOOM_IN, 4 = ZOOM_OUT
                this.sendTouchEvent(x, y, actionType);
                this.initialPinchDistance = currentDistance;
            }
        }
    }

    handleTouchEnd(e) {
        if (e.touches.length < 2) {
            this.isPinching = false;
        }
    }
}
