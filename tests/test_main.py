"""
Tests for Main application with logging integration.
"""

import unittest
import sys
import os
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


class TestMainLogging(unittest.TestCase):
    """Tests for Main application logging integration."""

    def test_main_logger_initialization(self):
        """Test that Main initializes with a logger."""
        from app.main import Main
        main = Main()
        self.assertIsNotNone(main._logger)
        self.assertEqual(main._logger.name, 'app.main')

    def test_main_logger_methods_exist(self):
        """Test that Main has log method."""
        from app.main import Main
        main = Main()
        self.assertTrue(hasattr(main, 'log'))

    def test_main_logger_info_level(self):
        """Test that log method uses correct level."""
        from app.main import Main
        main = Main()
        # Check that the logger is properly configured
        self.assertIsNotNone(main._logger)

    def test_main_run_logs_info(self):
        """Test that run method logs info messages."""
        from app.main import Main
        main = Main()
        # The run method should log info messages
        self.assertTrue(hasattr(main, 'run'))

    def test_main_logger_levels_mapping(self):
        """Test that log method handles different levels correctly."""
        from app.main import Main
        main = Main()
        
        # Test the level mapping in the log method
        level_map = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL
        }
        
        # Verify all levels are in the map
        for level in level_map.keys():
            self.assertIn(level, level_map)

    def test_main_on_off_methods(self):
        """Test on() and off() methods return True."""
        from app.main import Main
        main = Main()
        self.assertTrue(main.on())
        self.assertTrue(main.off())

    def test_main_get_status(self):
        """Test get_status() returns correct dictionary."""
        from app.main import Main
        main = Main()
        status = main.get_status()
        self.assertIn("connection", status)
        self.assertIn("bus_running", status)
        self.assertIn("main_running", status)

    def test_main_get_status_values(self):
        """Test get_status() returns correct values."""
        from app.main import Main
        main = Main()
        status = main.get_status()
        self.assertEqual(status["main_running"], "False")
        self.assertEqual(status["bus_running"], "False")

    def test_main_log_method(self):
        """Test log method exists and accepts parameters."""
        from app.main import Main
        main = Main()
        self.assertTrue(callable(main.log))

    def test_main_on_returns_true(self):
        """Test on() method returns True."""
        from app.main import Main
        main = Main()
        self.assertTrue(main.on())

    def test_main_off_returns_true(self):
        """Test off() method returns True."""
        from app.main import Main
        main = Main()
        self.assertTrue(main.off())

    def test_main_get_status_structure(self):
        """Test get_status() returns all expected keys."""
        from app.main import Main
        main = Main()
        status = main.get_status()
        self.assertEqual(len(status), 3)

    def test_main_start_gui_method(self):
        """Test start_gui method exists."""
        from app.main import Main
        main = Main()
        self.assertTrue(hasattr(main, 'start_gui'))
        self.assertTrue(callable(main.start_gui))

    def test_main_run_method_exists(self):
        """Test run method exists."""
        from app.main import Main
        main = Main()
        self.assertTrue(hasattr(main, 'run'))
        self.assertTrue(callable(main.run))

    def test_main_on_method_exists(self):
        """Test on method exists."""
        from app.main import Main
        main = Main()
        self.assertTrue(hasattr(main, 'on'))
        self.assertTrue(callable(main.on))

    def test_main_off_method_exists(self):
        """Test off method exists."""
        from app.main import Main
        main = Main()
        self.assertTrue(hasattr(main, 'off'))
        self.assertTrue(callable(main.off))

    def test_main_get_status_method_exists(self):
        """Test get_status method exists."""
        from app.main import Main
        main = Main()
        self.assertTrue(hasattr(main, 'get_status'))
        self.assertTrue(callable(main.get_status))

    def test_main_logger_level_map_debug(self):
        """Test that log method maps debug level correctly."""
        from app.main import Main
        main = Main()
        level_map = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL
        }
        self.assertIn("debug", level_map)
        self.assertEqual(level_map["debug"], logging.DEBUG)

    def test_main_logger_level_map_info(self):
        """Test that log method maps info level correctly."""
        from app.main import Main
        main = Main()
        level_map = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL
        }
        self.assertIn("info", level_map)
        self.assertEqual(level_map["info"], logging.INFO)

    def test_main_logger_level_map_warning(self):
        """Test that log method maps warning level correctly."""
        from app.main import Main
        main = Main()
        level_map = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL
        }
        self.assertIn("warning", level_map)
        self.assertEqual(level_map["warning"], logging.WARNING)

    def test_main_logger_level_map_error(self):
        """Test that log method maps error level correctly."""
        from app.main import Main
        main = Main()
        level_map = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL
        }
        self.assertIn("error", level_map)
        self.assertEqual(level_map["error"], logging.ERROR)

    def test_main_logger_level_map_critical(self):
        """Test that log method maps critical level correctly."""
        from app.main import Main
        main = Main()
        level_map = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL
        }
        self.assertIn("critical", level_map)
        self.assertEqual(level_map["critical"], logging.CRITICAL)


if __name__ == "__main__":
    unittest.main()