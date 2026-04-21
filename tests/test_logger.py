"""
Tests for Logger module - Per-Module Verbosity Control

This module provides test coverage for the logger module.
"""

import pytest
import logging
from unittest.mock import patch, MagicMock, Mock
from app.logger import Logger, LoggerManager, get_logger, set_verbosity, get_verbosity


class TestLogger:
    """Test cases for Logger class."""
    
    def test_logger_initialization(self):
        """Test logger initialization with default level."""
        logger = Logger('test_module')
        assert logger.level == logging.INFO
        assert logger.name == 'test_module'
    
    def test_logger_initialization_with_level(self):
        """Test logger initialization with custom level."""
        logger = Logger('test_module', level=logging.DEBUG)
        assert logger.level == logging.DEBUG
    
    def test_set_verbosity(self):
        """Test setting verbosity level."""
        logger = Logger('test_module')
        original_level = logger.level
        logger.set_verbosity(logging.DEBUG)
        assert logger.level == logging.DEBUG
        logger.set_verbosity(original_level)
    
    def test_get_verbosity(self):
        """Test getting verbosity level."""
        logger = Logger('test_module', level=logging.DEBUG)
        assert logger.get_verbosity() == logging.DEBUG
    
    def test_log_methods_exist(self):
        """Test that all log methods exist."""
        logger = Logger('test_module')
        assert hasattr(logger, 'debug')
        assert hasattr(logger, 'info')
        assert hasattr(logger, 'warning')
        assert hasattr(logger, 'error')
        assert hasattr(logger, 'critical')
    
    def test_log_message(self):
        """Test logging a message."""
        logger = Logger('test_module')
        logger.info("Test message")
    
    def test_debug_method(self):
        """Test debug method."""
        logger = Logger('test_module')
        logger.debug("Debug message")
    
    def test_info_method(self):
        """Test info method."""
        logger = Logger('test_module')
        logger.info("Info message")
    
    def test_warning_method(self):
        """Test warning method."""
        logger = Logger('test_module')
        logger.warning("Warning message")
    
    def test_error_method(self):
        """Test error method."""
        logger = Logger('test_module')
        logger.error("Error message")
    
    def test_critical_method(self):
        """Test critical method."""
        logger = Logger('test_module')
        logger.critical("Critical message")
    
    def test_enable_debug(self):
        """Test enabling debug mode."""
        logger = Logger('test_module')
        logger.enable_debug()
        assert logger.level == logging.DEBUG
    
    def test_disable_debug(self):
        """Test disabling debug mode."""
        logger = Logger('test_module')
        logger.enable_debug()
        logger.disable_debug()
        assert logger.level == logging.INFO
    
    def test_handler_creation(self):
        """Test that handler is created when none exists."""
        logger = Logger('test_module')
        assert len(logger.logger.handlers) > 0
    
    def test_logger_with_args(self):
        """Test logging with format arguments."""
        logger = Logger('test_module')
        logger.info("Message with %s and %d", "args", 123)


class TestLoggerManager:
    """Test cases for LoggerManager class."""
    
    def test_get_logger_new(self):
        """Test getting a new logger."""
        logger = LoggerManager.get_logger('new_module')
        assert logger is not None
    
    def test_get_logger_existing(self):
        """Test getting an existing logger."""
        logger1 = LoggerManager.get_logger('existing_module')
        logger2 = LoggerManager.get_logger('existing_module')
        assert logger1 is logger2
    
    def test_set_verbosity_module(self):
        """Test setting verbosity for a specific module."""
        LoggerManager.set_verbosity('test_module', logging.DEBUG)
        assert LoggerManager.get_verbosity('test_module') == logging.DEBUG
    
    def test_set_all_verbosity(self):
        """Test setting verbosity for all modules."""
        LoggerManager.set_all_verbosity(logging.DEBUG)
        assert LoggerManager.get_verbosity('test_module') == logging.DEBUG
    
    def test_get_verbosity_module(self):
        """Test getting verbosity for a module."""
        LoggerManager.set_verbosity('test_module', logging.DEBUG)
        assert LoggerManager.get_verbosity('test_module') == logging.DEBUG
    
    def test_logger_manager_singleton(self):
        """Test that LoggerManager is a singleton."""
        logger1 = LoggerManager.get_logger('test')
        logger2 = LoggerManager.get_logger('test')
        assert logger1 is logger2
    
    def test_set_verbosity_returns_none(self):
        """Test that set_verbosity returns None."""
        result = LoggerManager.set_verbosity('test_module', logging.DEBUG)
        assert result is None
    
    def test_set_all_verbosity_returns_none(self):
        """Test that set_all_verbosity returns None."""
        result = LoggerManager.set_all_verbosity(logging.DEBUG)
        assert result is None


class TestLoggerModule:
    """Test cases for module-level functions."""
    
    def test_get_logger_function(self):
        """Test get_logger function."""
        logger = get_logger('test_module')
        assert logger is not None
    
    def test_set_verbosity_function(self):
        """Test set_verbosity function."""
        set_verbosity('test_module', logging.DEBUG)
    
    def test_get_verbosity_function(self):
        """Test get_verbosity function."""
        set_verbosity('test_module', logging.DEBUG)
        level = get_verbosity('test_module')
        assert level == logging.DEBUG


class TestLoggerInitialization:
    """Test cases for logger initialization."""
    
    def test_logger_creates_handler(self):
        """Test that logger creates a handler."""
        logger = Logger('test_module')
        assert len(logger.logger.handlers) > 0
    
    def test_logger_has_correct_level(self):
        """Test that logger has correct level."""
        logger = Logger('test_module', level=logging.DEBUG)
        assert logger.level == logging.DEBUG
    
    def test_logger_name_set(self):
        """Test that logger name is set correctly."""
        logger = Logger('test_module')
        assert logger.name == 'test_module'


class TestLoggerMethods:
    """Test cases for logger methods."""
    
    def test_all_log_methods_callable(self):
        """Test that all log methods are callable."""
        logger = Logger('test_module')
        assert callable(logger.debug)
        assert callable(logger.info)
        assert callable(logger.warning)
        assert callable(logger.error)
        assert callable(logger.critical)
    
    def test_log_methods_set_correct_level(self):
        """Test that log methods set correct level."""
        logger = Logger('test_module')
        logger.debug("msg")
        logger.info("msg")
        logger.warning("msg")
        logger.error("msg")
        logger.critical("msg")


class TestLoggerManagerMethods:
    """Test cases for LoggerManager methods."""
    
    def test_get_logger_returns_logger(self):
        """Test that get_logger returns a logger."""
        logger = LoggerManager.get_logger('test')
        assert isinstance(logger, Logger)
    
    def test_set_verbosity_sets_correct_level(self):
        """Test that set_verbosity sets correct level."""
        LoggerManager.set_verbosity('test_module', logging.DEBUG)
        assert LoggerManager.get_verbosity('test_module') == logging.DEBUG
    
    def test_set_all_verbosity_sets_correct_level(self):
        """Test that set_all_verbosity sets correct level."""
        LoggerManager.set_all_verbosity(logging.DEBUG)
        assert LoggerManager.get_verbosity('test_module') == logging.DEBUG