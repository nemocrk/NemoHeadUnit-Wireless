"""
Logger Module - Per-Module Verbosity Control

This module provides a flexible logging system that allows each component
to control its own verbosity level independently.
"""

import logging
from typing import Dict, Optional, Any
from enum import IntEnum


class LogLevel(IntEnum):
    """Log level enumeration for verbosity control."""
    NOTSET = logging.NOTSET
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


class Logger:
    """
    Logger class providing per-module verbosity control.
    
    Each module can independently configure its log level,
    allowing fine-grained control over logging verbosity.
    """
    
    def __init__(self, name: str, level: int = logging.INFO) -> None:
        """
        Initialize the logger for a specific module.
        
        Args:
            name: Module name (typically __name__ from the module)
            level: Log level for this module
        """
        self.name = name
        self.level = level
        self.logger = logging.getLogger(name)
        self.logger.setLevel(self.level)
        
        # Add handler if none exists
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
    
    def set_verbosity(self, level: int) -> None:
        """
        Set the verbosity level for this module.
        
        Args:
            level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        self.logger.setLevel(level)
        self.level = level
    
    def get_verbosity(self) -> int:
        """Get the current verbosity level."""
        return self.level
    
    def log(self, level: int, message: str, *args, **kwargs) -> None:
        """
        Log a message with the appropriate level.
        
        Args:
            level: Log level
            message: Log message
            *args: Additional positional arguments
            **kwargs: Additional keyword arguments
        """
        self.logger.log(level, message, *args, **kwargs)
    
    # Convenience methods for each log level
    def debug(self, msg: str, *args, **kwargs) -> None:
        self.log(logging.DEBUG, msg, *args, **kwargs)
    
    def info(self, msg: str, *args, **kwargs) -> None:
        self.log(logging.INFO, msg, *args, **kwargs)
    
    def warning(self, msg: str, *args, **kwargs) -> None:
        self.log(logging.WARNING, msg, *args, **kwargs)
    
    def error(self, msg: str, *args, **kwargs) -> None:
        self.log(logging.ERROR, msg, *args, **kwargs)
    
    def critical(self, msg: str, *args, **kwargs) -> None:
        self.log(logging.CRITICAL, msg, *args, **kwargs)
    
    def enable_debug(self) -> None:
        """Enable debug logging for this module."""
        self.set_verbosity(logging.DEBUG)
    
    def disable_debug(self) -> None:
        """Disable debug logging for this module."""
        self.set_verbosity(logging.INFO)


class LoggerManager:
    """
    Manager class to control all loggers centrally.
    
    Provides a centralized way to configure logging levels
    across all modules.
    """
    
    _loggers: Dict[str, Logger] = {}
    
    @classmethod
    def get_logger(cls, name: str) -> Logger:
        """Get or create a logger for the given name."""
        if name not in cls._loggers:
            cls._loggers[name] = Logger(name)
        return cls._loggers[name]
    
    @classmethod
    def set_verbosity(cls, module_name: str, level: int) -> None:
        """Set the verbosity level for a specific module."""
        logger = cls.get_logger(module_name)
        logger.set_verbosity(level)
    
    @classmethod
    def set_all_verbosity(cls, level: int) -> None:
        """Set the verbosity level for all modules."""
        for logger in cls._loggers.values():
            logger.set_verbosity(level)
    
    @classmethod
    def get_verbosity(cls, module_name: str) -> int:
        """Get the verbosity level for a specific module."""
        logger = cls.get_logger(module_name)
        return logger.get_verbosity()


# Create default loggers for common modules
def setup_default_loggers() -> None:
    """Setup default loggers for common modules."""
    LoggerManager.get_logger('app.main')
    LoggerManager.get_logger('app.connection')
    LoggerManager.get_logger('app.base_interface')
    LoggerManager.get_logger('app.message_bus')
    LoggerManager.get_logger('app.gui.main_window')


# Convenience functions
def get_logger(name: str) -> Logger:
    """Get a logger by name."""
    return LoggerManager.get_logger(name)


def set_verbosity(module_name: str, level: int) -> None:
    """Set verbosity for a specific module."""
    LoggerManager.set_verbosity(module_name, level)


def get_verbosity(module_name: str) -> int:
    """Get verbosity level for a specific module."""
    return LoggerManager.get_verbosity(module_name)