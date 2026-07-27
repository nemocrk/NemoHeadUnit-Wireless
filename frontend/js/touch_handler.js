/**
 * Multi-Touch & Gesture Handler with Touch Sampling for Android Auto Touchscreen Input
 */

export class TouchHandler {
    constructor(canvasElement, apiEndpoint = '/api/channels/input/touch', sampleIntervalMs = 30) {
        this.canvas = canvasElement;
        this.apiEndpoint = apiEndpoint;
        this.sampleIntervalMs = sampleIntervalMs; // Throttling interval for DRAG events (~33Hz)
        
        this.lastDragTime = 0;

        this.bindEvents();
    }

    bindEvents() {
        const c = this.canvas;

        // HTML5 Touch Events (primary for multi-touch touchscreens)
        c.addEventListener('touchstart', (e) => this.handleTouchStart(e), { passive: false });
        c.addEventListener('touchmove', (e) => this.handleTouchMove(e), { passive: false });
        c.addEventListener('touchend', (e) => this.handleTouchEnd(e), { passive: false });
        c.addEventListener('touchcancel', (e) => this.handleTouchEnd(e), { passive: false });

        // Fallback Pointer / Mouse Events (for desktop testing / mouse interaction)
        c.addEventListener('pointerdown', (e) => this.handlePointerDown(e));
        c.addEventListener('pointermove', (e) => this.handlePointerMove(e));
        c.addEventListener('pointerup', (e) => this.handlePointerUp(e));
        c.addEventListener('pointercancel', (e) => this.handlePointerUp(e));
    }

    getNormalizedCoords(clientX, clientY) {
        const rect = this.canvas.getBoundingClientRect();
        const canvasWidth = this.canvas.width || 1280;
        const canvasHeight = this.canvas.height || 720;

        if (rect.width <= 0 || rect.height <= 0) {
            return { x: 0, y: 0 };
        }

        const videoRatio = canvasWidth / canvasHeight;
        const elementRatio = rect.width / rect.height;

        let renderedWidth = rect.width;
        let renderedHeight = rect.height;
        let offsetX = 0;
        let offsetY = 0;

        if (elementRatio > videoRatio) {
            // Pillarboxing (black bars on left and right)
            renderedWidth = rect.height * videoRatio;
            offsetX = (rect.width - renderedWidth) / 2;
        } else if (elementRatio < videoRatio) {
            // Letterboxing (black bars on top and bottom)
            renderedHeight = rect.width / videoRatio;
            offsetY = (rect.height - renderedHeight) / 2;
        }

        // Relative coordinates within the active video projection frame
        const relX = clientX - rect.left - offsetX;
        const relY = clientY - rect.top - offsetY;

        // Map to intrinsic video projection resolution [0, canvasWidth - 1] x [0, canvasHeight - 1]
        const projX = Math.round((relX / renderedWidth) * canvasWidth);
        const projY = Math.round((relY / renderedHeight) * canvasHeight);

        return {
            x: Math.max(0, Math.min(projX, canvasWidth - 1)),
            y: Math.max(0, Math.min(projY, canvasHeight - 1))
        };
    }

    sendMultiTouchEvent(pointers, action, actionIndex = 0) {
        // Android Auto TouchAction Enum values:
        // 0 = PRESS (ACTION_DOWN)
        // 1 = RELEASE (ACTION_UP)
        // 2 = DRAG (ACTION_MOVE)
        // 5 = POINTER_DOWN (ACTION_POINTER_DOWN)
        // 6 = POINTER_UP (ACTION_POINTER_UP)
        const payload = {
            pointers: pointers,
            action: action,
            action_index: actionIndex
        };
        fetch(this.apiEndpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }).catch((err) => console.debug('Touch send error:', err));
    }

    handleTouchStart(e) {
        e.preventDefault();
        const activeList = [];
        for (let i = 0; i < e.touches.length; i++) {
            const t = e.touches[i];
            const { x, y } = this.getNormalizedCoords(t.clientX, t.clientY);
            activeList.push({ x, y, pointer_id: t.identifier });
        }

        // Determine if first touch (PRESS) or additional touch (POINTER_DOWN)
        for (let i = 0; i < e.changedTouches.length; i++) {
            const changed = e.changedTouches[i];
            const changedIdx = activeList.findIndex(p => p.pointer_id === changed.identifier);
            if (activeList.length === 1 && changedIdx === 0) {
                // First pointer down -> PRESS (0)
                this.sendMultiTouchEvent(activeList, 0, 0);
            } else if (changedIdx >= 0) {
                // Additional pointer down -> POINTER_DOWN (5)
                this.sendMultiTouchEvent(activeList, 5, changedIdx);
            }
        }
    }

    handleTouchMove(e) {
        e.preventDefault();
        const now = performance.now();
        // Touch sampling: throttle continuous DRAG events to sampleIntervalMs (default 30ms)
        if (now - this.lastDragTime < this.sampleIntervalMs) {
            return;
        }
        this.lastDragTime = now;

        const activeList = [];
        for (let i = 0; i < e.touches.length; i++) {
            const t = e.touches[i];
            const { x, y } = this.getNormalizedCoords(t.clientX, t.clientY);
            activeList.push({ x, y, pointer_id: t.identifier });
        }

        if (activeList.length > 0) {
            // Continuous move -> DRAG (2)
            this.sendMultiTouchEvent(activeList, 2, 0);
        }
    }

    handleTouchEnd(e) {
        e.preventDefault();
        // Active list remaining after touch release
        const remainingList = [];
        for (let i = 0; i < e.touches.length; i++) {
            const t = e.touches[i];
            const { x, y } = this.getNormalizedCoords(t.clientX, t.clientY);
            remainingList.push({ x, y, pointer_id: t.identifier });
        }

        for (let i = 0; i < e.changedTouches.length; i++) {
            const changed = e.changedTouches[i];
            const { x, y } = this.getNormalizedCoords(changed.clientX, changed.clientY);
            
            if (remainingList.length === 0) {
                // Last pointer lifted -> RELEASE (1)
                const releaseList = [{ x, y, pointer_id: changed.identifier }];
                this.sendMultiTouchEvent(releaseList, 1, 0);
            } else {
                // Non-last pointer lifted -> POINTER_UP (6)
                this.sendMultiTouchEvent(remainingList, 6, 0);
            }
        }
    }

    handlePointerDown(e) {
        if (e.pointerType === 'touch') return; // Handled by HTML5 touch events
        if (e.target !== this.canvas) return;
        const { x, y } = this.getNormalizedCoords(e.clientX, e.clientY);
        this.sendMultiTouchEvent([{ x, y, pointer_id: 0 }], 0, 0); // PRESS (0)
    }

    handlePointerMove(e) {
        if (e.pointerType === 'touch') return;
        if (e.target !== this.canvas || e.buttons !== 1) return;
        const now = performance.now();
        if (now - this.lastDragTime < this.sampleIntervalMs) {
            return;
        }
        this.lastDragTime = now;
        const { x, y } = this.getNormalizedCoords(e.clientX, e.clientY);
        this.sendMultiTouchEvent([{ x, y, pointer_id: 0 }], 2, 0); // DRAG (2)
    }

    handlePointerUp(e) {
        if (e.pointerType === 'touch') return;
        if (e.target !== this.canvas) return;
        const { x, y } = this.getNormalizedCoords(e.clientX, e.clientY);
        this.sendMultiTouchEvent([{ x, y, pointer_id: 0 }], 1, 0); // RELEASE (1)
    }
}
