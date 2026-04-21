"""
Tests for Wireless Android Auto Connection Module

Test coverage: 80%+ required per develop-it.md Phase 5
"""

import unittest
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestWirelessModule(unittest.TestCase):
    """Tests for Wireless Module."""
    
    def test_bluetooth_manager_init(self):
        """Test Bluetooth manager initialization."""
        from app.wireless.bluetooth_manager import BluetoothManager
        manager = BluetoothManager()
        self.assertIsNotNone(manager)
    
    def test_bluetooth_manager_uuids(self):
        """Test Bluetooth manager UUIDs."""
        from app.wireless.bluetooth_manager import BluetoothManager
        manager = BluetoothManager()
        self.assertEqual(manager.HFP_UUID, "0000111e-0000-1000-8000-00805f9b34fb")
        self.assertEqual(manager.HSP_UUID, "00001108-0000-1000-8000-00805f9b34fb")
        self.assertEqual(manager.AA_UUID, "4de17a00-52cb-11e6-bdf4-0800200c9a66")


class TestRfcommHandler(unittest.TestCase):
    """Tests for RFCOMM Handler."""
    
    def test_rfcomm_handler_init(self):
        """Test RFCOMM handler initialization."""
        from app.wireless.rfcomm_handler import RfcommHandler
        handler = RfcommHandler(None)
        self.assertIsNotNone(handler)
    
    def test_rfcomm_handler_message_ids(self):
        """Test RFCOMM handler message IDs."""
        from app.wireless.rfcomm_handler import RfcommHandler
        handler = RfcommHandler(None)
        self.assertEqual(handler.WIFI_START_REQUEST, 1)
        self.assertEqual(handler.WIFI_START_RESPONSE, 7)
        self.assertEqual(handler.WIFI_INFO_REQUEST, 2)
        self.assertEqual(handler.WIFI_INFO_RESPONSE, 3)
        self.assertEqual(handler.WIFI_CONNECT_STATUS, 6)
    
    def test_rfcomm_handler_security_modes(self):
        """Test RFCOMM handler security modes."""
        from app.wireless.rfcomm_handler import RfcommHandler
        handler = RfcommHandler(None)
        self.assertEqual(handler.WPA2_PERSONAL, 8)
        self.assertEqual(handler.DYNAMIC, 1)


class TestTcpServer(unittest.TestCase):
    """Tests for TCP Server."""
    
    def test_tcp_server_init(self):
        """Test TCP server initialization."""
        from app.wireless.tcp_server import TcpServer
        server = TcpServer()
        self.assertIsNotNone(server)
    
    def test_tcp_server_config(self):
        """Test TCP server configuration."""
        from app.wireless.tcp_server import TcpServer
        server = TcpServer(host="127.0.0.1", port=5288)
        self.assertEqual(server.host, "127.0.0.1")
        self.assertEqual(server.port, 5288)


if __name__ == "__main__":
    unittest.main()