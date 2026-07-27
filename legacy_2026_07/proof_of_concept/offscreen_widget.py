import sys
import os
import argparse
import queue
import threading
import json
import zmq
import time

# Set Qt platform to headless/offscreen before creating QApplication
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QSlider, QLabel
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QTimer, Qt
import PyQt6.QtCore as QtCore
from PyQt6.QtGui import QMouseEvent, QKeyEvent, QPainter, QColor, QRadialGradient, QLinearGradient, QTouchEvent, QEventPoint, QPointingDevice
from shared_buffer import DoubleSharedBuffer

class ZmqDealerWorker(threading.Thread):
    def __init__(self, port, widget_id, layout_hints, sig_resize, sig_input, sig_shutdown, sig_swap_ack):
        super().__init__()
        self.port = port
        self.widget_id = widget_id
        self.layout_hints = layout_hints
        self.sig_resize = sig_resize
        self.sig_input = sig_input
        self.sig_shutdown = sig_shutdown
        self.sig_swap_ack = sig_swap_ack
        self.send_queue = queue.Queue()
        self.running = True
        self.daemon = True
        self.last_pong_time = time.time()
        self.last_ping_sent = 0

    def run(self):
        ctx = zmq.Context()
        socket = ctx.socket(zmq.DEALER)
        # Give dealer unique ZMQ identity
        socket.setsockopt(zmq.IDENTITY, self.widget_id.encode('utf-8'))
        socket.connect(f"tcp://127.0.0.1:{self.port}")
        
        poller = zmq.Poller()
        poller.register(socket, zmq.POLLIN)
        
        # Send registration frame
        reg_payload = {
            "type": "register",
            "widget_id": self.widget_id,
            "layout_hints": self.layout_hints
        }
        socket.send_multipart([b"", json.dumps(reg_payload).encode('utf-8')], flags=zmq.NOBLOCK)
        print(f"[Widget {self.widget_id}] Registered on port {self.port} with hints: {self.layout_hints}")
        
        # 15-second initial grace period before checking heartbeat timeouts
        self.last_pong_time = time.time() + 15.0
        
        while self.running:
            # Poll ZMQ for messages (10ms timeout)
            socks = dict(poller.poll(10))
            if socket in socks and socks[socket] == zmq.POLLIN:
                try:
                    parts = socket.recv_multipart()
                    if len(parts) >= 2:
                        payload_bytes = parts[1]
                        payload = json.loads(payload_bytes.decode('utf-8'))
                        
                        mtype = payload.get("type")
                        if mtype == "pong":
                            self.last_pong_time = time.time()
                        elif mtype == "resize":
                            w = payload.get("width")
                            h = payload.get("height")
                            self.sig_resize.emit(w, h)
                        elif mtype == "input":
                            event_data = payload.get("event")
                            self.sig_input.emit(event_data)
                        elif mtype == "shutdown":
                            self.sig_shutdown.emit()
                        elif mtype == "swap_ack":
                            self.sig_swap_ack.emit()
                except Exception as e:
                    print(f"[Widget {self.widget_id}] Error handling packet: {e}")
            
            # Send periodic heartbeats (pings)
            now = time.time()
            if now - self.last_ping_sent >= 1.0:
                self.send_msg({"type": "ping", "widget_id": self.widget_id})
                self.last_ping_sent = now
            
            # Check for heartbeat loss / timeout
            if now - self.last_pong_time > 10.0:
                print(f"[Widget {self.widget_id}] Heartbeat lost. Main Window exited? Shutting down...")
                self.sig_shutdown.emit()
                self.running = False
                break
            
            # Send queued messages
            while not self.send_queue.empty():
                try:
                    msg_dict = self.send_queue.get_nowait()
                    msg_bytes = json.dumps(msg_dict).encode('utf-8')
                    socket.send_multipart([b"", msg_bytes], flags=zmq.NOBLOCK)
                    self.send_queue.task_done()
                except queue.Empty:
                    break
                except Exception as e:
                    print(f"[Widget {self.widget_id}] Error sending packet: {e}")
                    
        socket.close()
        ctx.term()
        print(f"[Widget {self.widget_id}] ZMQ worker thread stopped")

    def send_msg(self, payload):
        self.send_queue.put(payload)

    def stop(self):
        self.running = False


class BouncingBallWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ball_x = 150
        self.ball_y = 150
        self.ball_dx = 5
        self.ball_dy = 4
        self.ball_radius = 20
        self.setStyleSheet("background-color: #11111b;")

    def update_position(self):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
            
        self.ball_x += self.ball_dx
        self.ball_y += self.ball_dy
        
        # Boundaries bounce checks
        if self.ball_x - self.ball_radius <= 0:
            self.ball_x = self.ball_radius
            self.ball_dx = -self.ball_dx
        elif self.ball_x + self.ball_radius >= w:
            self.ball_x = w - self.ball_radius
            self.ball_dx = -self.ball_dx
            
        if self.ball_y - self.ball_radius <= 0:
            self.ball_y = self.ball_radius
            self.ball_dy = -self.ball_dy
        elif self.ball_y + self.ball_radius >= h:
            self.ball_y = h - self.ball_radius
            self.ball_dy = -self.ball_dy

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Deep theme background
        bg_grad = QLinearGradient(0, 0, 0, self.height())
        bg_grad.setColorAt(0.0, QColor("#1e1e2e"))
        bg_grad.setColorAt(1.0, QColor("#11111b"))
        painter.fillRect(self.rect(), bg_grad)
        
        # Glowing bouncing ball
        ball_grad = QRadialGradient(self.ball_x, self.ball_y, self.ball_radius)
        ball_grad.setColorAt(0.0, QColor("#f5c2e7"))
        ball_grad.setColorAt(0.7, QColor("#cba6f7"))
        ball_grad.setColorAt(1.0, QColor("#89b4fa"))
        
        painter.setBrush(ball_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(
            int(self.ball_x - self.ball_radius), 
            int(self.ball_y - self.ball_radius), 
            self.ball_radius * 2, 
            self.ball_radius * 2
        )
        
        # Process context display
        painter.setPen(QColor("#cdd6f4"))
        painter.drawText(20, 30, "Bouncing Ball Process (60 FPS Offscreen)")
        painter.drawText(20, 50, f"Position: ({int(self.ball_x)}, {int(self.ball_y)})")


class InteractiveFormWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.count = 0
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(15)
        
        # Title
        title = QLabel("Interactive Form Process")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #89b4fa;")
        layout.addWidget(title)
        
        # Text input
        layout.addWidget(QLabel("Type and focus will route here:"))
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("Type some text...")
        self.text_input.setStyleSheet("""
            QLineEdit {
                background-color: #313244;
                border: 1px solid #45475a;
                border-radius: 4px;
                padding: 6px;
                color: #cdd6f4;
            }
            QLineEdit:focus {
                border: 1px solid #89b4fa;
            }
        """)
        layout.addWidget(self.text_input)
        
        # Echo
        self.echo = QLabel("Echo: (empty)")
        self.echo.setStyleSheet("color: #a6adc8; font-style: italic;")
        self.text_input.textChanged.connect(lambda t: self.echo.setText(f"Echo: {t}"))
        layout.addWidget(self.echo)
        
        # Button increment
        self.btn = QPushButton("Click Count: 0")
        self.btn.setStyleSheet("""
            QPushButton {
                background-color: #a6e3a1;
                color: #11111b;
                font-weight: bold;
                border-radius: 4px;
                padding: 8px;
            }
            QPushButton:hover {
                background-color: #94e2d5;
            }
            QPushButton:pressed {
                background-color: #74c7ec;
            }
        """)
        self.btn.clicked.connect(self.on_click)
        layout.addWidget(self.btn)
        
        # Slider
        slider_layout = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(50)
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 6px;
                background: #313244;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #f5e0dc;
                width: 16px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 8px;
            }
        """)
        self.slider_label = QLabel("Slider: 50")
        self.slider_label.setStyleSheet("color: #cdd6f4; min-width: 80px;")
        
        self.slider.valueChanged.connect(lambda v: self.slider_label.setText(f"Slider: {v}"))
        slider_layout.addWidget(self.slider)
        slider_layout.addWidget(self.slider_label)
        layout.addLayout(slider_layout)
        
        layout.addStretch()
        self.setStyleSheet("background-color: #1e1e2e;")

    def on_click(self):
        self.count += 1
        self.btn.setText(f"Click Count: {self.count}")


class StatusOverlayWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.start_time = time.time()
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 10, 15, 10)
        layout.setSpacing(5)
        
        # Heading / Title
        title = QLabel("System Status Overlay")
        title.setStyleSheet("font-size: 14px; font-weight: bold; color: #f5c2e7;")
        layout.addWidget(title)
        
        # Status details
        self.status_label = QLabel("Uptime: 0.0s")
        self.status_label.setStyleSheet("font-size: 12px; color: #a6e3a1;")
        layout.addWidget(self.status_label)
        
        self.pid_label = QLabel(f"PID: {os.getpid()}")
        self.pid_label.setStyleSheet("font-size: 11px; color: #89b4fa;")
        layout.addWidget(self.pid_label)
        
        # Glassmorphic premium style sheet
        self.setStyleSheet("""
            StatusOverlayWidget {
                background-color: rgba(30, 30, 46, 180);
                border: 2px solid rgba(245, 194, 231, 200);
                border-radius: 12px;
            }
            QLabel {
                background: transparent;
            }
        """)
        
        # Timer to update uptime
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_status)
        self.timer.start(100) # update every 100ms
        
    def update_status(self):
        elapsed = time.time() - self.start_time
        self.status_label.setText(f"Uptime: {elapsed:.1f}s")


class WidgetHostSignals(QObject):
    sig_resize = pyqtSignal(int, int)
    sig_input = pyqtSignal(dict)
    sig_shutdown = pyqtSignal()
    sig_swap_ack = pyqtSignal()


class OffscreenWidgetApp(QObject):
    def __init__(self, widget_id, port, layout_hints):
        super().__init__()
        self.widget_id = widget_id
        self.port = port
        self.layout_hints = layout_hints
        
        self.current_w = 400
        self.current_h = 300
        self.active_idx = 0
        self.captured_widget = None
        self.pending_ack = False
        self.needs_redraw = False
        
        # Build layout
        if self.widget_id == "interactive_form":
            self.widget = InteractiveFormWidget()
        elif self.widget_id == "bouncing_ball":
            self.widget = BouncingBallWidget()
        elif self.widget_id == "status_overlay":
            self.widget = StatusOverlayWidget()
        else:
            raise ValueError(f"Unknown widget: {widget_id}")
            
        self.widget.resize(self.current_w, self.current_h)
        
        # Instantiate DoubleSharedBuffer (Creator)
        self.shm_buffer = DoubleSharedBuffer(self.widget_id, create=True)
        
        # ZMQ worker setup
        self.signals = WidgetHostSignals()
        self.signals.sig_resize.connect(self.on_resize)
        self.signals.sig_input.connect(self.on_input)
        self.signals.sig_shutdown.connect(self.on_shutdown)
        self.signals.sig_swap_ack.connect(self.on_swap_ack)
        
        self.worker = ZmqDealerWorker(
            self.port, 
            self.widget_id, 
            self.layout_hints,
            self.signals.sig_resize, 
            self.signals.sig_input, 
            self.signals.sig_shutdown,
            self.signals.sig_swap_ack
        )
        self.worker.start()
        
        # Show the widget headlessly
        self.widget.show()
        
        # Set repaint triggers
        if self.widget_id == "bouncing_ball":
            self.anim_timer = QTimer()
            self.anim_timer.timeout.connect(self.update_animation)
            self.anim_timer.start(16)  # ~60 FPS update
        elif self.widget_id == "status_overlay":
            self.refresh_timer = QTimer()
            self.refresh_timer.timeout.connect(self.render_and_swap)
            self.refresh_timer.start(100)  # ~10 FPS update
        else:
            # Low-frequency polling timer for blinking cursors or state changes
            self.refresh_timer = QTimer()
            self.refresh_timer.timeout.connect(self.render_and_swap)
            self.refresh_timer.start(250)

    @pyqtSlot()
    def on_shutdown(self):
        print(f"[Widget {self.widget_id}] Received ZMQ shutdown signal. Quitting QApplication...")
        QApplication.quit()

    @pyqtSlot()
    def on_swap_ack(self):
        self.pending_ack = False
        if self.needs_redraw:
            self.render_and_swap()

    @pyqtSlot(int, int)
    def on_resize(self, w, h):
        self.current_w = w
        self.current_h = h
        self.widget.resize(w, h)
        self.render_and_swap()

    @pyqtSlot(dict)
    def on_input(self, event_data):
        etype = event_data["type"]
        
        if etype in ("MousePress", "MouseRelease", "MouseMove"):
            x, y = event_data["x"], event_data["y"]
            
            # If mouse is pressed, lock to target child widget (mouse capture)
            if etype == "MousePress":
                target = self.widget.childAt(x, y)
                if not target:
                    target = self.widget
                self.captured_widget = target
            elif self.captured_widget:
                target = self.captured_widget
            else:
                target = self.widget.childAt(x, y)
                if not target:
                    target = self.widget
            
            local_pos = target.mapFrom(self.widget, QtCore.QPoint(x, y))
            
            # Handle focus change inside offscreen widget tree on MousePress
            if etype == "MousePress":
                if target.focusPolicy() & Qt.FocusPolicy.TabFocus:
                    target.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
            
            qt_type = {
                "MousePress": QtCore.QEvent.Type.MouseButtonPress,
                "MouseRelease": QtCore.QEvent.Type.MouseButtonRelease,
                "MouseMove": QtCore.QEvent.Type.MouseMove
            }[etype]
            
            button = Qt.MouseButton(event_data["button"])
            buttons = Qt.MouseButton(event_data["buttons"])
            modifiers = Qt.KeyboardModifier(event_data["modifiers"])
            
            # Clear mouse capture if all buttons are released
            if etype == "MouseRelease" and event_data["buttons"] == 0:
                self.captured_widget = None
            
            ev = QMouseEvent(
                qt_type,
                QtCore.QPointF(local_pos),
                button,
                buttons,
                modifiers
            )
            QtCore.QCoreApplication.postEvent(target, ev)
            
        elif etype == "TouchEvent":
            touch_type = QtCore.QEvent.Type(event_data["touch_type"])
            points_data = event_data["points"]
            modifiers = Qt.KeyboardModifier(event_data["modifiers"])
            
            # Reconstruct Event Points
            q_points = []
            for pt in points_data:
                state = QEventPoint.State(pt["state"])
                scene_pos = QtCore.QPointF(pt["x"], pt["y"])
                global_pos = QtCore.QPointF(pt["gx"], pt["gy"])
                q_pt = QEventPoint(pt["id"], state, scene_pos, global_pos)
                q_points.append(q_pt)
                
            if q_points:
                # Find target widget for routing (based on first touch point)
                first_pt = points_data[0]
                fx, fy = int(first_pt["x"]), int(first_pt["y"])
                target = self.widget.childAt(fx, fy)
                if not target:
                    target = self.widget
                    
                # Walk up target tree to see if it accepts touch
                curr = target
                accepts_touch = False
                while curr:
                    if curr.testAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents):
                        accepts_touch = True
                        target = curr
                        break
                    curr = curr.parentWidget()
                    
                if accepts_touch:
                    # Post real touch event
                    ev = QTouchEvent(
                        touch_type,
                        QPointingDevice.primaryPointingDevice(),
                        modifiers,
                        q_points
                    )
                    QtCore.QCoreApplication.postEvent(target, ev)
                else:
                    # Synthesize Mouse Event (Touch-to-Mouse fallback)
                    first_state = QEventPoint.State(first_pt["state"])
                    mouse_type = None
                    if first_state == QEventPoint.State.Pressed:
                        mouse_type = QtCore.QEvent.Type.MouseButtonPress
                    elif first_state == QEventPoint.State.Updated:
                        mouse_type = QtCore.QEvent.Type.MouseMove
                    elif first_state == QEventPoint.State.Released:
                        mouse_type = QtCore.QEvent.Type.MouseButtonRelease
                        
                    if mouse_type is not None:
                        # Manage mouse capture simulation during touch drags
                        if mouse_type == QtCore.QEvent.Type.MouseButtonPress:
                            self.captured_widget = self.widget.childAt(fx, fy) or self.widget
                            if self.captured_widget.focusPolicy() & Qt.FocusPolicy.TabFocus:
                                self.captured_widget.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)
                                
                        target_widget = self.captured_widget if self.captured_widget else (self.widget.childAt(fx, fy) or self.widget)
                        
                        if mouse_type == QtCore.QEvent.Type.MouseButtonRelease:
                            self.captured_widget = None
                            
                        local_pos = target_widget.mapFrom(self.widget, QtCore.QPoint(fx, fy))
                        button = Qt.MouseButton.LeftButton if mouse_type in (QtCore.QEvent.Type.MouseButtonPress, QtCore.QEvent.Type.MouseButtonRelease) else Qt.MouseButton.NoButton
                        buttons = Qt.MouseButton.LeftButton if mouse_type != QtCore.QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
                        
                        ev = QMouseEvent(
                            mouse_type,
                            QtCore.QPointF(local_pos),
                            button,
                            buttons,
                            modifiers
                        )
                        QtCore.QCoreApplication.postEvent(target_widget, ev)
            
        elif etype in ("KeyPress", "KeyRelease"):
            target = QApplication.focusWidget()
            if not target:
                target = self.widget
                
            qt_type = {
                "KeyPress": QtCore.QEvent.Type.KeyPress,
                "KeyRelease": QtCore.QEvent.Type.KeyRelease
            }[etype]
            
            key = Qt.Key(event_data["key"])
            modifiers = Qt.KeyboardModifier(event_data["modifiers"])
            text = event_data["text"]
            
            ev = QKeyEvent(
                qt_type,
                key,
                modifiers,
                text
            )
            QtCore.QCoreApplication.postEvent(target, ev)
            
        # Schedule offscreen render pass immediately after event dispatch
        QtCore.QTimer.singleShot(0, self.render_and_swap)

    def update_animation(self):
        self.widget.update_position()
        self.widget.update()
        self.render_and_swap()

    def render_and_swap(self):
        if not self.shm_buffer:
            return
            
        # Throttling/flow control to prevent rendering faster than the UI paints (avoids flickering)
        if self.pending_ack:
            self.needs_redraw = True
            return
            
        self.pending_ack = True
        self.needs_redraw = False
            
        # Draw into back buffer
        back_idx = 1 if self.active_idx == 0 else 0
        img = self.shm_buffer.get_image(back_idx, self.current_w, self.current_h)
        img.fill(QtCore.Qt.GlobalColor.transparent)
        
        # Render offscreen widget directly to shared memory QImage
        self.widget.render(img)
        
        self.active_idx = back_idx
        
        # Notify collector main window of the updated frame buffer
        self.worker.send_msg({
            "type": "swap_buffer",
            "widget_id": self.widget_id,
            "buffer_index": self.active_idx,
            "width": self.current_w,
            "height": self.current_h
        })

    def shutdown(self):
        self.worker.stop()
        self.worker.join(timeout=1.0)
        self.shm_buffer.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--widget-id", required=True, choices=["interactive_form", "bouncing_ball", "status_overlay"])
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--docking", default="center")
    parser.add_argument("--z-index", type=int, default=0)
    parser.add_argument("--priority", type=int, default=0)
    parser.add_argument("--min-width", type=int, default=None)
    parser.add_argument("--min-height", type=int, default=None)
    parser.add_argument("--max-width", type=int, default=None)
    parser.add_argument("--max-height", type=int, default=None)
    parser.add_argument("--aspect-ratio", type=float, default=None)
    args = parser.parse_args()
    
    layout_hints = {
        "docking": args.docking,
        "z_index": args.z_index,
        "priority": args.priority,
        "min_width": args.min_width,
        "min_height": args.min_height,
        "max_width": args.max_width,
        "max_height": args.max_height,
        "aspect_ratio": args.aspect_ratio
    }
    
    app = QApplication(sys.argv)
    
    widget_app = OffscreenWidgetApp(args.widget_id, args.port, layout_hints)
    
    try:
        sys.exit(app.exec())
    finally:
        widget_app.shutdown()
