import sys
import queue
import threading
import json
import zmq
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, Qt, QPointF, QEvent, QRect
import PyQt6.QtCore as QtCore
from PyQt6.QtGui import QPainter, QMouseEvent, QKeyEvent, QTouchEvent, QEventPoint
from shared_buffer import DoubleSharedBuffer

class ZmqWorkerSignals(QObject):
    # Signals to communicate from background ZMQ thread to PyQt main GUI thread
    registered = pyqtSignal(str, bytes, dict)             # widget_id, client_id, layout_hints
    buffer_swapped = pyqtSignal(str, int, int, int)  # widget_id, buffer_index, width, height

class ZmqWorker(threading.Thread):
    def __init__(self, port, signals):
        super().__init__()
        self.port = port
        self.signals = signals
        self.send_queue = queue.Queue()
        self.running = True
        self.daemon = True

    def run(self):
        ctx = zmq.Context()
        socket = ctx.socket(zmq.ROUTER)
        # Bind to localhost
        socket.bind(f"tcp://127.0.0.1:{self.port}")
        
        poller = zmq.Poller()
        poller.register(socket, zmq.POLLIN)
        
        print(f"[ZmqWorker] Server listening on port {self.port}")
        
        while self.running:
            # Poll for input messages (10ms timeout)
            socks = dict(poller.poll(10))
            if socket in socks and socks[socket] == zmq.POLLIN:
                try:
                    parts = socket.recv_multipart()
                    if len(parts) >= 3:
                        client_id = parts[0]
                        # Frame 1 is empty, frame 2 is message payload
                        payload_bytes = parts[2]
                        payload = json.loads(payload_bytes.decode('utf-8'))
                        
                        mtype = payload.get("type")
                        widget_id = payload.get("widget_id")
                        
                        if mtype == "register":
                            layout_hints = payload.get("layout_hints", {})
                            self.signals.registered.emit(widget_id, client_id, layout_hints)
                        elif mtype == "swap_buffer":
                            buf_idx = payload.get("buffer_index")
                            w = payload.get("width")
                            h = payload.get("height")
                            self.signals.buffer_swapped.emit(widget_id, buf_idx, w, h)
                        elif mtype == "ping":
                            self.send_msg(client_id, {"type": "pong"})
                except Exception as e:
                    print(f"[ZmqWorker] Error parsing message: {e}")
            
            # Send queued outbound messages
            while not self.send_queue.empty():
                try:
                    client_id, msg_dict = self.send_queue.get_nowait()
                    msg_bytes = json.dumps(msg_dict).encode('utf-8')
                    socket.send_multipart([client_id, b"", msg_bytes], flags=zmq.NOBLOCK)
                    self.send_queue.task_done()
                except queue.Empty:
                    break
                except Exception as e:
                    print(f"[ZmqWorker] Error sending message: {e}")
                    
        socket.close()
        ctx.term()
        print("[ZmqWorker] Thread terminated")

    def send_msg(self, client_id, payload):
        self.send_queue.put((client_id, payload))

    def stop(self):
        self.running = False


def solve_dimensions(target_w, target_h, hints):
    min_w = float(hints["min_width"]) if hints.get("min_width") is not None else None
    max_w = float(hints["max_width"]) if hints.get("max_width") is not None else None
    min_h = float(hints["min_height"]) if hints.get("min_height") is not None else None
    max_h = float(hints["max_height"]) if hints.get("max_height") is not None else None
    ar = float(hints["aspect_ratio"]) if hints.get("aspect_ratio") is not None else None
    
    # If no aspect ratio, just clamp target_w and target_h to min/max
    if ar is None or ar <= 0:
        w = target_w
        h = target_h
        if min_w is not None: w = max(w, min_w)
        if max_w is not None: w = min(w, max_w)
        if min_h is not None: h = max(h, min_h)
        if max_h is not None: h = min(h, max_h)
        return max(1, int(w)), max(1, int(h))
        
    # We have aspect ratio. Calculate valid range for h.
    h_min = 0.0
    if min_h is not None:
        h_min = max(h_min, float(min_h))
    if min_w is not None:
        h_min = max(h_min, float(min_w) / ar)
        
    h_max = float('inf')
    if max_h is not None:
        h_max = min(h_max, float(max_h))
    if max_w is not None:
        h_max = min(h_max, float(max_w) / ar)
        
    # Fit target h inside the valid [h_min, h_max] range
    target_h_space = min(float(target_h), float(target_w) / ar)
    
    if h_min <= h_max:
        h = max(h_min, min(h_max, target_h_space))
    else:
        # Conflict: valid range is empty. Fallback to h_min.
        h = h_min
        
    w = h * ar
    return max(1, int(w)), max(1, int(h))


class LayoutContainer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.recalculate_callback = None
        from PyQt6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        
    def event(self, event):
        if event.type() == QtCore.QEvent.Type.LayoutRequest:
            # Swallow bottom-up layout requests to prevent feedback loops
            return True
        return super().event(event)
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        print(f"[LayoutContainer] resizeEvent: old={event.oldSize()}, new={event.size()}")
        if self.recalculate_callback:
            self.recalculate_callback()

    def updateGeometry(self):
        # Override and do nothing to stop bottom-up size constraint propagation to MainWindow
        pass

    def sizeHint(self):
        return self.size()

    def minimumSizeHint(self):
        return QtCore.QSize(100, 100)


class OffscreenWidgetHost(QWidget):
    def __init__(self, widget_id: str, parent=None):
        super().__init__(parent)
        self.widget_id = widget_id
        self.client_id = None
        self.worker = None
        
        self.shm_buffer = None
        self.active_buf_idx = -1
        self.active_w = 0
        self.active_h = 0
        
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        
        # Prevent any size propagation up to parent layout
        from PyQt6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def sizeHint(self):
        return QtCore.QSize(0, 0)

    def minimumSizeHint(self):
        return QtCore.QSize(0, 0)

    def set_client_info(self, client_id, worker):
        self.client_id = client_id
        self.worker = worker
        
        # Attach to the double-buffered shared memory created by the widget process
        try:
            self.shm_buffer = DoubleSharedBuffer(self.widget_id, create=False)
            print(f"[Host {self.widget_id}] Attached to shared memory successfully.")
            
            # Send initial geometry constraint to the widget
            self.send_resize(self.width(), self.height())
        except Exception as e:
            print(f"[Host {self.widget_id}] Shared memory attachment failed: {e}")

    def send_resize(self, w, h):
        if self.worker and self.client_id:
            # We enforce min size of 1x1 to prevent Qt engine assert errors
            self.worker.send_msg(self.client_id, {
                "type": "resize",
                "width": max(1, w),
                "height": max(1, h)
            })

    def update_frame(self, buf_idx, w, h):
        self.active_buf_idx = buf_idx
        self.active_w = w
        self.active_h = h
        self.update()  # Request Qt repaint

    def paintEvent(self, event):
        painter = QPainter(self)
        
        # If we have focus, draw a distinct border highlight
        if self.hasFocus():
            painter.setPen(Qt.GlobalColor.cyan)
        else:
            painter.setPen(Qt.GlobalColor.darkGray)
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        
        if self.shm_buffer and self.active_buf_idx != -1:
            try:
                # Wrap the shared memory buffer directly in a zero-copy QImage
                img = self.shm_buffer.get_image(self.active_buf_idx, self.active_w, self.active_h)
                # Paint it starting at (0, 0)
                painter.drawImage(0, 0, img)
            except Exception as e:
                painter.setPen(Qt.GlobalColor.red)
                painter.drawText(20, 40, f"Paint error: {e}")
        else:
            # Draw placeholder state
            painter.setPen(Qt.GlobalColor.gray)
            painter.drawText(20, 40, f"[{self.widget_id}] Connecting / Rendering...")

    def event(self, event):
        if event.type() in (
            QEvent.Type.TouchBegin,
            QEvent.Type.TouchUpdate,
            QEvent.Type.TouchEnd,
            QEvent.Type.TouchCancel
        ):
            self.send_touch_event(event)
            event.accept()
            return True
        return super().event(event)

    def send_touch_event(self, event: QTouchEvent):
        if not (self.worker and self.client_id):
            return
        
        points_data = []
        for p in event.points():
            # Clamp coordinates to the bounds of this host widget
            cx = max(0.0, min(p.position().x(), float(self.width())))
            cy = max(0.0, min(p.position().y(), float(self.height())))
            points_data.append({
                "id": p.id(),
                "state": p.state().value,
                "x": cx,
                "y": cy,
                "gx": p.globalPosition().x(),
                "gy": p.globalPosition().y()
            })
            
        self.worker.send_msg(self.client_id, {
            "type": "input",
            "event": {
                "type": "TouchEvent",
                "touch_type": event.type().value,
                "points": points_data,
                "modifiers": event.modifiers().value
            }
        })

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.send_resize(self.width(), self.height())

    # Forward Mouse Events
    def mousePressEvent(self, event: QMouseEvent):
        self.setFocus()  # Grab keyboard focus on click
        self.send_mouse_event("MousePress", event)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self.send_mouse_event("MouseRelease", event)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        self.send_mouse_event("MouseMove", event)
        event.accept()

    # Forward Keyboard Events
    def keyPressEvent(self, event: QKeyEvent):
        self.send_key_event("KeyPress", event)
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent):
        self.send_key_event("KeyRelease", event)
        event.accept()

    def send_mouse_event(self, etype, event: QMouseEvent):
        if not (self.worker and self.client_id):
            return
        pos = event.position()
        # Clamp coordinates to the bounds of this host widget
        cx = max(0, min(int(pos.x()), self.width()))
        cy = max(0, min(int(pos.y()), self.height()))
        self.worker.send_msg(self.client_id, {
            "type": "input",
            "event": {
                "type": etype,
                "x": cx,
                "y": cy,
                "button": event.button().value if hasattr(event.button(), "value") else int(event.button()),
                "buttons": event.buttons().value if hasattr(event.buttons(), "value") else int(event.buttons()),
                "modifiers": event.modifiers().value if hasattr(event.modifiers(), "value") else int(event.modifiers())
            }
        })

    def send_key_event(self, etype, event: QKeyEvent):
        if not (self.worker and self.client_id):
            return
        self.worker.send_msg(self.client_id, {
            "type": "input",
            "event": {
                "type": etype,
                "key": event.key().value if hasattr(event.key(), "value") else int(event.key()),
                "text": event.text(),
                "modifiers": event.modifiers().value if hasattr(event.modifiers(), "value") else int(event.modifiers())
            }
        })


class CentralWidget(QWidget):
    def event(self, event):
        if event.type() == QtCore.QEvent.Type.LayoutRequest:
            return True
        return super().event(event)

    def sizeHint(self):
        return self.size()

    def minimumSizeHint(self):
        return QtCore.QSize(100, 100)


class MainWindow(QMainWindow):
    def event(self, event):
        if event.type() == QtCore.QEvent.Type.LayoutRequest:
            return True
        return super().event(event)

    def __init__(self, port=5555):
        super().__init__()
        self.port = port
        self.setWindowTitle("Multiprocess UI Integration Collector (Main Window)")
        self.resize(1200, 800)
        
        main_widget = CentralWidget()
        self.setCentralWidget(main_widget)
        
        # Heading
        self.header = QLabel("Multiprocess UI Collector (PyQt6 + ZMQ + Zero-Copy Shared Memory)", main_widget)
        self.header.setStyleSheet("font-size: 16px; font-weight: bold; color: #89b4fa; padding: 5px;")
        
        self.info = QLabel("Click inside a widget to focus it. Text input will route to the focused widget.", main_widget)
        self.info.setStyleSheet("color: #a6adc8; font-size: 12px; padding-left: 5px;")
        
        # Custom layout container
        self.container = LayoutContainer(main_widget)
        self.container.recalculate_callback = self.recalculate_layout
        
        # Hosts dictionary (dynamically populated)
        self.hosts = {}
        self.registration_counter = 0
        
        # Theme styling
        self.setStyleSheet("""
            QMainWindow {
                background-color: #11111b;
            }
            QLabel {
                color: #cdd6f4;
            }
        """)
        
        # Setup ZMQ Worker Thread
        self.signals = ZmqWorkerSignals()
        self.signals.registered.connect(self.on_widget_registered)
        self.signals.buffer_swapped.connect(self.on_buffer_swapped)
        
        self.worker = ZmqWorker(self.port, self.signals)
        self.worker.start()

    @pyqtSlot(str, bytes, dict)
    def on_widget_registered(self, widget_id, client_id, layout_hints):
        print(f"[MainWindow] Registered offscreen process: {widget_id} with hints: {layout_hints}")
        
        if widget_id not in self.hosts:
            host = OffscreenWidgetHost(widget_id, self.container)
            self.registration_counter += 1
            host.registration_sequence = self.registration_counter
            self.hosts[widget_id] = host
            
        host = self.hosts[widget_id]
        host.layout_hints = layout_hints
        host.set_client_info(client_id, self.worker)
        
        self.recalculate_layout()

    def recalculate_layout(self):
        container_w = self.container.width()
        container_h = self.container.height()
        print(f"[recalculate_layout] Starting: container={container_w}x{container_h}")
        if container_w <= 0 or container_h <= 0:
            print("[recalculate_layout] Exit: container size is <= 0")
            return
            
        # Group active hosts by z_index
        active_hosts = [h for h in self.hosts.values() if hasattr(h, "layout_hints") and h.layout_hints is not None]
        print(f"[recalculate_layout] Active hosts: {[h.widget_id for h in active_hosts]}")
        
        layers = {}
        for host in active_hosts:
            z = host.layout_hints.get("z_index", 0)
            if z not in layers:
                layers[z] = []
            layers[z].append(host)
            
        # Sort each layer by (-priority, registration_sequence)
        for z in layers:
            layers[z].sort(key=lambda h: (-h.layout_hints.get("priority", 0), h.registration_sequence))
            
        # Layout each layer
        for z in sorted(layers.keys()):
            # Initialize remaining rectangle for this layer
            R = QRect(0, 0, container_w, container_h)
            print(f"[recalculate_layout] Layer {z} start: R={R}")
            
            for host in layers[z]:
                hints = host.layout_hints
                w, h = solve_dimensions(R.width(), R.height(), hints)
                
                dock = hints.get("docking", "center").lower()
                print(f"[recalculate_layout] Host {host.widget_id} ({dock}): target size={w}x{h}")
                
                if dock == "left":
                    x = R.left()
                    y = R.top() + (R.height() - h) // 2
                    host.setGeometry(x, y, w, h)
                    R.setLeft(R.left() + w)
                elif dock == "right":
                    x = R.right() - w + 1
                    y = R.top() + (R.height() - h) // 2
                    host.setGeometry(x, y, w, h)
                    R.setRight(R.right() - w)
                elif dock == "top":
                    x = R.left() + (R.width() - w) // 2
                    y = R.top()
                    host.setGeometry(x, y, w, h)
                    R.setTop(R.top() + h)
                elif dock == "bottom":
                    x = R.left() + (R.width() - w) // 2
                    y = R.bottom() - h + 1
                    host.setGeometry(x, y, w, h)
                    R.setBottom(R.bottom() - h)
                elif dock == "top-left":
                    x = R.left()
                    y = R.top()
                    host.setGeometry(x, y, w, h)
                    R.setLeft(R.left() + w)
                    R.setTop(R.top() + h)
                elif dock == "top-right":
                    x = R.right() - w + 1
                    y = R.top()
                    host.setGeometry(x, y, w, h)
                    R.setRight(R.right() - w)
                    R.setTop(R.top() + h)
                elif dock == "bottom-left":
                    x = R.left()
                    y = R.bottom() - h + 1
                    host.setGeometry(x, y, w, h)
                    R.setLeft(R.left() + w)
                    R.setBottom(R.bottom() - h)
                elif dock == "bottom-right":
                    x = R.right() - w + 1
                    y = R.bottom() - h + 1
                    host.setGeometry(x, y, w, h)
                    R.setRight(R.right() - w)
                    R.setBottom(R.bottom() - h)
                else: # center
                    x = R.left() + (R.width() - w) // 2
                    y = R.top() + (R.height() - h) // 2
                    host.setGeometry(x, y, w, h)
                    
                print(f"[recalculate_layout] Placed {host.widget_id} at {x},{y} size {w}x{h}. New R={R}")
                    
            # Enforce proper z-order drawing sequence
            for host in layers[z]:
                host.raise_()
                host.show()
        print("[recalculate_layout] Completed successfully")

    @pyqtSlot(str, int, int, int)
    def on_buffer_swapped(self, widget_id, buf_idx, w, h):
        if widget_id in self.hosts:
            self.hosts[widget_id].update_frame(buf_idx, w, h)
            # Send swap acknowledgment back to the widget process
            host = self.hosts[widget_id]
            if host.worker and host.client_id:
                host.worker.send_msg(host.client_id, {
                    "type": "swap_ack",
                    "widget_id": widget_id
                })

    def closeEvent(self, event):
        import time
        # Send shutdown signal to child widgets
        print("[MainWindow] Sending shutdown signal to all hosted widgets...")
        for host in self.hosts.values():
            if host.worker and host.client_id:
                host.worker.send_msg(host.client_id, {"type": "shutdown"})
        
        # Give them a brief moment to process before ZMQ unbinds
        time.sleep(0.2)

        # Shut down worker thread cleanly
        self.worker.stop()
        self.worker.join(timeout=1.0)
        
        # Detach hosts from shared memory segments
        for host in self.hosts.values():
            if host.shm_buffer:
                host.shm_buffer.cleanup()
                
    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self.centralWidget().width()
        h = self.centralWidget().height()
        if w > 0 and h > 0:
            self.header.setGeometry(10, 10, w - 20, 30)
            self.info.setGeometry(10, 45, w - 20, 20)
            self.container.setGeometry(10, 75, w - 20, h - 85)

if __name__ == "__main__":
    import signal
    from PyQt6.QtCore import QTimer
    
    app = QApplication(sys.argv)
    window = MainWindow()
    
    # Clean shutdown on SIGINT and SIGTERM
    def handle_signal(sig, frame):
        print(f"[MainWindow] Received signal {sig}, closing window...")
        window.close()
        
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    
    # Periodically return control to Python interpreter to run signal handlers
    timer = QTimer()
    timer.start(100)
    timer.timeout.connect(lambda: None)
    
    window.show()
    sys.exit(app.exec())
