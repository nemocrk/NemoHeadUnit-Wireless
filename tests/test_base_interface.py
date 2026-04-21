"""
Tests for Base Interface and Components.
"""

import unittest
import sys
import os

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestBaseInterface(unittest.TestCase):
    """Tests for BaseInterface."""
    
    def test_base_interface_imports(self):
        """Test that base interface can be imported."""
        from app.base_interface import BaseInterface
        self.assertIsNotNone(BaseInterface)
    
    def test_message_bus_creation(self):
        """Test that message bus can be created."""
        from app.message_bus import MessageBus
        bus = MessageBus()
        self.assertIsNotNone(bus)
        self.assertTrue(hasattr(bus, '_queue'))


class TestConnectionManager(unittest.TestCase):
    """Tests for ConnectionManager."""
    
    def test_connection_manager_creation(self):
        """Test that connection manager can be created."""
        from app.connection import ConnectionManager
        manager = ConnectionManager()
        self.assertIsNotNone(manager)
    
    def test_connection_manager_status(self):
        """Test that connection manager has correct initial status."""
        from app.connection import ConnectionManager
        manager = ConnectionManager()
        self.assertEqual(manager.status, "disconnected")
    
    def test_connection_manager_on_off(self):
        """Test that connection manager can start and stop."""
        from app.connection import ConnectionManager
        manager = ConnectionManager()
        self.assertTrue(manager.on())
        self.assertTrue(manager._bluetooth_thread is not None)
        self.assertTrue(manager._wifi_ap_thread is not None)
        self.assertTrue(manager.off())


class TestMain(unittest.TestCase):
    """Tests for Main application."""
    
    def test_main_creation(self):
        """Test that main application can be created."""
        from app.main import Main
        main = Main()
        self.assertIsNotNone(main)
    
    def test_main_status(self):
        """Test that main application has correct status."""
        from app.main import Main
        main = Main()
        status = main.get_status()
        self.assertIn("connection", status)
        self.assertIn("bus_running", status)
        self.assertIn("main_running", status)


if __name__ == "__main__":
    unittest.main()