import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import unittest
import sys
import asyncio
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QPointF, Qt, QEvent
from PyQt6.QtGui import QTouchEvent, QEventPoint, QPointingDevice

from backend.modules.qt6_gui.ui.video_viewport import VideoViewportWidget
from backend.modules.channel_manager.handlers.input_handler import InputChannelHandler


class DummyManager:
    def __init__(self):
        self.sent_frames = []
        import logging
        self.log = logging.getLogger("dummy_manager")

    def get_channel_id_for_type(self, ch_type):
        return 1

    async def send_wire_frame(self, channel_id, message_id, payload, encrypted=True):
        self.sent_frames.append((channel_id, message_id, payload))


class TestTouchHandling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()

    def setUp(self):
        self.widget = VideoViewportWidget()
        self.widget.resize(1280, 720)
        self.widget.frame_width = 1280
        self.widget.frame_height = 720
        self.emitted_events = []
        self.widget.touch_input_event.connect(lambda data: self.emitted_events.append(data))

    def _create_point(self, pid: int, state: QEventPoint.State, x: float, y: float) -> QEventPoint:
        pt = QEventPoint(pid, state, QPointF(x, y), QPointF(x, y))
        pt.setPosition(QPointF(x, y))
        return pt

    def test_single_touch_press_and_release(self):
        device = QPointingDevice.primaryPointingDevice()
        
        # 1. Finger 0 Pressed -> ACTION_DOWN (0)
        p0 = self._create_point(0, QEventPoint.State.Pressed, 100, 100)
        ev_down = QTouchEvent(QEvent.Type.TouchBegin, device, Qt.KeyboardModifier.NoModifier, [p0])
        self.widget.touchEvent(ev_down)
        
        self.assertEqual(len(self.emitted_events), 1)
        self.assertEqual(self.emitted_events[0]["action"], 0)
        self.assertEqual(self.emitted_events[0]["action_index"], 0)
        self.assertEqual(len(self.emitted_events[0]["pointers"]), 1)
        self.assertEqual(self.emitted_events[0]["pointers"][0]["x"], 100)

        # 2. Finger 0 Released -> ACTION_UP (1)
        p0_rel = self._create_point(0, QEventPoint.State.Released, 100, 100)
        ev_up = QTouchEvent(QEvent.Type.TouchEnd, device, Qt.KeyboardModifier.NoModifier, [p0_rel])
        self.widget.touchEvent(ev_up)

        self.assertEqual(len(self.emitted_events), 2)
        self.assertEqual(self.emitted_events[1]["action"], 1)
        self.assertEqual(self.emitted_events[1]["action_index"], 0)
        self.assertEqual(len(self.emitted_events[1]["pointers"]), 1)

    def test_multitouch_pointer_down_and_pointer_up(self):
        device = QPointingDevice.primaryPointingDevice()

        # 1. Finger 0 Pressed -> ACTION_DOWN (0)
        p0 = self._create_point(0, QEventPoint.State.Pressed, 200, 200)
        ev1 = QTouchEvent(QEvent.Type.TouchBegin, device, Qt.KeyboardModifier.NoModifier, [p0])
        self.widget.touchEvent(ev1)

        # 2. Finger 1 Pressed while Finger 0 Stationary -> ACTION_POINTER_DOWN (5)
        p0_stat = self._create_point(0, QEventPoint.State.Stationary, 200, 200)
        p1_press = self._create_point(1, QEventPoint.State.Pressed, 400, 400)
        ev2 = QTouchEvent(QEvent.Type.TouchUpdate, device, Qt.KeyboardModifier.NoModifier, [p0_stat, p1_press])
        self.widget.touchEvent(ev2)

        self.assertEqual(self.emitted_events[-1]["action"], 5)  # POINTER_DOWN
        self.assertEqual(self.emitted_events[-1]["action_index"], 1)
        self.assertEqual(len(self.emitted_events[-1]["pointers"]), 2)
        self.assertEqual(self.emitted_events[-1]["pointers"][1]["pointer_id"], 1)

        # 3. Finger 1 Released while Finger 0 Stationary -> ACTION_POINTER_UP (6)
        p0_stat2 = self._create_point(0, QEventPoint.State.Stationary, 200, 200)
        p1_rel = self._create_point(1, QEventPoint.State.Released, 400, 400)
        ev3 = QTouchEvent(QEvent.Type.TouchUpdate, device, Qt.KeyboardModifier.NoModifier, [p0_stat2, p1_rel])
        self.widget.touchEvent(ev3)

        self.assertEqual(self.emitted_events[-1]["action"], 6)  # POINTER_UP
        self.assertEqual(self.emitted_events[-1]["action_index"], 1)
        self.assertEqual(len(self.emitted_events[-1]["pointers"]), 2)
        self.assertEqual(self.emitted_events[-1]["pointers"][1]["pointer_id"], 1)

    def test_input_handler_encodes_multitouch_protobuf(self):
        from protos.oaa.input.InputEventIndicationMessage_pb2 import InputEventIndication
        
        manager = DummyManager()
        handler = InputChannelHandler(manager)

        pointers = [
            {"x": 150, "y": 250, "pointer_id": 0},
            {"x": 350, "y": 450, "pointer_id": 1},
        ]
        asyncio.run(handler.handle_touch_event(action=5, pointers=pointers, action_index=1))

        self.assertEqual(len(manager.sent_frames), 1)
        ch_id, msg_id, payload = manager.sent_frames[0]
        
        indication = InputEventIndication()
        indication.ParseFromString(payload)
        
        self.assertEqual(indication.touch_event.touch_action, 5)
        self.assertEqual(indication.touch_event.action_index, 1)
        self.assertEqual(len(indication.touch_event.touch_location), 2)
        self.assertEqual(indication.touch_event.touch_location[0].x, 150)
        self.assertEqual(indication.touch_event.touch_location[1].x, 350)


if __name__ == "__main__":
    unittest.main()
