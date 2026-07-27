import pytest

try:
    from PyQt6.QtCore import QEvent
    from PyQt6.QtWidgets import QWidget
except Exception:
    pytest.skip("PyQt6 widgets are not available", allow_module_level=True)

from shared.shm_helper import inject_input_event


class RecordingWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.mouse_events = []

    def mousePressEvent(self, event):
        self.mouse_events.append(("press", event.position().x(), event.position().y()))
        event.accept()

    def mouseMoveEvent(self, event):
        self.mouse_events.append(("move", event.position().x(), event.position().y()))
        event.accept()

    def mouseReleaseEvent(self, event):
        self.mouse_events.append(("release", event.position().x(), event.position().y()))
        event.accept()


def _drain_events(qt_app):
    qt_app.sendPostedEvents(None, QEvent.Type.MouseButtonPress)
    qt_app.sendPostedEvents(None, QEvent.Type.MouseMove)
    qt_app.sendPostedEvents(None, QEvent.Type.MouseButtonRelease)
    qt_app.processEvents()


def test_drag_stays_with_pressed_child_when_pointer_leaves_bounds(qt_app):
    root = QWidget()
    root.resize(200, 100)
    child = RecordingWidget(root)
    child.setGeometry(20, 20, 40, 20)

    inject_input_event(root, {"type": "press", "x": 30, "y": 30})
    inject_input_event(root, {"type": "move", "x": 150, "y": 80, "buttons": 1})
    inject_input_event(root, {"type": "release", "x": 150, "y": 80})
    _drain_events(qt_app)

    assert [event[0] for event in child.mouse_events] == ["press", "move", "release"]
    assert child.mouse_events[1][1:] == (130.0, 60.0)


def test_drag_coordinates_are_clamped_to_root_before_child_dispatch(qt_app):
    root = QWidget()
    root.resize(200, 100)
    child = RecordingWidget(root)
    child.setGeometry(20, 20, 40, 20)

    inject_input_event(root, {"type": "press", "x": 30, "y": 30})
    inject_input_event(root, {"type": "move", "x": 500, "y": 500, "buttons": 1})
    inject_input_event(root, {"type": "release", "x": 500, "y": 500})
    _drain_events(qt_app)

    assert [event[0] for event in child.mouse_events] == ["press", "move", "release"]
    assert child.mouse_events[1][1:] == (180.0, 80.0)
