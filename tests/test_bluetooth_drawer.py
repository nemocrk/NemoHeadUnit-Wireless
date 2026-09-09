import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys
import unittest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from backend.modules.qt6_gui.ui.drawers.bluetooth_drawer import BluetoothDrawerWidget


class TestBluetoothDrawer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not QApplication.instance():
            cls.app = QApplication(sys.argv)
        else:
            cls.app = QApplication.instance()

    def test_bluetooth_drawer_ui_and_status_update(self):
        from unittest.mock import MagicMock, patch
        with patch("backend.modules.qt6_gui.ui.drawers.bluetooth_drawer.BluetoothStreamThread"), \
             patch("backend.modules.qt6_gui.ui.drawers.bluetooth_drawer.BluetoothScanThread"), \
             patch("backend.modules.qt6_gui.ui.drawers.bluetooth_drawer.BluetoothActionThread"):

            drawer = BluetoothDrawerWidget()
            drawer.show()

            # 1. Update status with pairing request and devices
            data = {
                "discovering": True,
                "stage_label": "Discovering",
                "pairing_pin": "123456",
                "pairing_device": "Pixel 8",
                "known_aa_devices": ["AA:BB:CC:DD:EE:01"],
                "ignored_devices": ["AA:BB:CC:DD:EE:02"],
                "paired_devices": [
                    {"name": "Pixel 8", "address": "AA:BB:CC:DD:EE:01"}
                ],
                "discovered_devices": [
                    {"name": "Old Phone", "address": "AA:BB:CC:DD:EE:02"},
                    {"name": "New Device", "address": "AA:BB:CC:DD:EE:03"},
                ]
            }
            drawer._on_status_updated(data)
            assert drawer.pairing_card.isVisible()
            assert drawer.device_list.count() == 3
            assert "Pixel 8" in drawer.lbl_pairing_title.text()
            assert "123456" in drawer.lbl_pairing_pin.text()

            # 2. Confirm PIN button
            drawer._on_confirm_pin_clicked()
            assert not drawer.pairing_card.isVisible()

            # 3. Reject PIN button
            drawer.active_pairing_device = "Pixel 8"
            drawer._on_reject_pin_clicked()
            assert not drawer.pairing_card.isVisible()

            # 4. Select paired item and trigger connect
            drawer.device_list.setCurrentRow(0)
            drawer._on_connect_pair_clicked()
            assert "Connecting to" in drawer.lbl_status.text()

            # 5. Select unpaired item and trigger pair
            drawer.device_list.setCurrentRow(2)
            drawer._on_connect_pair_clicked()
            assert "Initiating pairing" in drawer.lbl_status.text()

            # 6. Toggle ignore
            drawer.device_list.setCurrentRow(1)
            drawer._on_toggle_ignore_clicked()

            # 7. Forget/Remove device
            drawer.device_list.setCurrentRow(0)
            drawer._on_remove_clicked()
            assert "Removing device" in drawer.lbl_status.text()

            # 8. Scan clicked
            drawer._on_scan_clicked()
            assert "Initiating Bluetooth discovery" in drawer.lbl_status.text()

            # 9. Empty devices update
            drawer._on_status_updated({"paired_devices": [], "discovered_devices": []})
            assert drawer.device_list.count() == 1
            drawer.stop_stream()

    def test_bluetooth_threads(self):
        from unittest.mock import MagicMock, patch
        import io
        from backend.modules.qt6_gui.ui.drawers.bluetooth_drawer import (
            BluetoothStreamThread,
            BluetoothScanThread,
            BluetoothActionThread,
        )

        # 1. Scan thread
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            scan_thread = BluetoothScanThread()
            scan_thread.run()
            mock_urlopen.assert_called_once()
            assert "discover" in mock_urlopen.call_args[0][0].full_url

        # 2. Action thread
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            action_thread = BluetoothActionThread("pair", {"device_address": "11:22:33:44:55:66"})
            action_thread.run()
            mock_urlopen.assert_called_once()
            assert "pair" in mock_urlopen.call_args[0][0].full_url

        # 3. Stream thread
        with patch("urllib.request.urlopen") as mock_urlopen:
            stream_thread = BluetoothStreamThread()
            received_data = []
            stream_thread.status_updated.connect(lambda d: received_data.append(d))

            class FakeStream:
                def __iter__(self):
                    yield b'data: {"discovering": true}\n'
                    stream_thread.stop()
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass

            def fake_urlopen(req, timeout=None):
                return FakeStream()

            mock_urlopen.side_effect = fake_urlopen
            stream_thread.run()
            assert len(received_data) == 1
            assert received_data[0]["discovering"] is True

    def test_bluetooth_drawer_empty_selections_and_toast(self):
        from unittest.mock import MagicMock, patch
        with patch("backend.modules.qt6_gui.ui.drawers.bluetooth_drawer.BluetoothStreamThread"):
            drawer = BluetoothDrawerWidget()
            drawer.show()

            # Empty selection checks
            drawer.device_list.clear()
            drawer._on_connect_pair_clicked()
            assert "Select a device" in drawer.lbl_status.text()

            drawer._on_toggle_ignore_clicked()
            assert "Select a device" in drawer.lbl_status.text()

            drawer._on_remove_clicked()
            assert "Select a device" in drawer.lbl_status.text()

            # Toast notification trigger
            mock_window = MagicMock()
            mock_toast = MagicMock()
            mock_window.toast_widget = mock_toast
            with patch.object(drawer, "window", return_value=mock_window):
                drawer._on_status_updated({
                    "pairing_pin": "999888",
                    "pairing_device": "TestPhone",
                })
                mock_toast.show_toast.assert_called_once()
                assert "999888" in mock_toast.show_toast.call_args[0][0]


