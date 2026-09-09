import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys
import unittest
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QApplication

from backend.modules.qt6_gui.ui.volume_popover import (
    VolumePopoverWidget,
    MediaStatusStreamThread,
    VolumeActionThread,
)


class TestVolumePopover(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()

    def test_media_status_stream_thread(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            thread = MediaStatusStreamThread()
            received = []
            thread.media_status_updated.connect(lambda d: received.append(d))

            class FakeStream:
                def __iter__(self):
                    yield b'data: {"volume": 65, "muted": false}\n'
                    thread.stop()
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass

            def fake_urlopen(req, timeout=None):
                return FakeStream()

            mock_urlopen.side_effect = fake_urlopen
            thread.run()
            assert len(received) == 1
            assert received[0]["volume"] == 65

    def test_volume_action_thread(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"volume": 90, "muted": false}'
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            thread = VolumeActionThread("up")
            received = []
            thread.volume_updated.connect(lambda v, m: received.append((v, m)))
            thread.run()

            assert len(received) == 1
            assert received[0] == (90, False)
            mock_urlopen.assert_called_once()
            assert "action=up" in mock_urlopen.call_args[0][0].full_url

    def test_volume_popover_widget_clicks_and_updates(self):
        with patch("backend.modules.qt6_gui.ui.volume_popover.VolumeActionThread"):
            popover = VolumePopoverWidget()
            # Prevent automatic thread spawning in showEvent
            popover.start_media_stream = MagicMock()
            popover.stop_media_stream = MagicMock()
            popover.show()

            assert popover.current_volume == 80
            assert popover.lbl_level.text() == "80%"

            # 1. Click Vol Up
            popover.btn_up.click()
            assert popover.current_volume == 85
            assert popover.lbl_level.text() == "85%"

            # 2. Click Vol Down
            popover.btn_down.click()
            assert popover.current_volume == 80
            assert popover.lbl_level.text() == "80%"

            # 3. Click Mute
            popover.btn_mute.click()
            assert popover.is_muted is True
            assert popover.lbl_level.text() == "Muted"

            # 4. Direct update_volume
            popover.update_volume(50, False)
            assert popover.current_volume == 50
            assert popover.lbl_level.text() == "50%"

            # 5. Media status update from SSE (simulating elapsed time > 1.5s)
            popover._last_user_action_time = 0.0
            popover._on_media_status_updated({"volume": 40, "muted": False})
            assert popover.current_volume == 40
            assert popover.lbl_level.text() == "40%"

            # Hide popover
            popover.hide()
