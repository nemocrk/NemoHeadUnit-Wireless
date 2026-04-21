"""
Message Bus for NemoHeadUnit-Wireless
Shared message bus architecture for internal parallelism.
"""

import threading
import time
from typing import Any, Callable, Dict, List, Optional
from queue import Queue
from dataclasses import dataclass

from app.logger import Logger, LoggerManager


@dataclass
class Message:
    """Represents a message in the message bus."""
    topic: str
    timestamp: float
    sender: str
    payload: Any
    priority: int = 0
    
    def __repr__(self):
        return f"Message(sender={self.sender}, priority={self.priority})"


class MessageBus:
    """
    Shared message bus for internal parallelism.
    All components connected to the message bus.
    
    Each component declares which thread it needs, and the bus executes
    handlers in the appropriate thread.
    """
    
    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Ensures that the MessageBus follows the Singleton pattern."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super(MessageBus, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        self._queue: Queue = Queue()
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._handlers: Dict[str, List[Callable]] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
        self._logger: Logger = LoggerManager.get_logger('app.message_bus')
        
        # Thread registry - maps thread names to thread objects
        # Includes main thread and all component threads
        self._thread_registry: Dict[str, threading.Thread] = {}
        self._thread_registry_lock = threading.Lock()
        
        # Thread affinity registry - maps handler names to thread names
        # Used to determine which thread to execute handlers in
        self._thread_affinity: Dict[str, str] = {}
        
        # Affinity queues - stores messages to be processed in specific threads
        self._affinity_queues: Dict[str, Queue] = {}
        
        # Register main thread
        self._register_thread("main", threading.current_thread())
    
        self._initialized = True
        
        # Subscribe to shutdown event
        self.subscribe('app.shutdown', self._on_shutdown)

    def register_handler(self, handler: Callable) -> None:
        """Registers a global handler for all messages."""
        with self._lock:
            self.subscribe("all", handler)
            self._logger.info("Registered Handler")
    
    def publish(self, topic: str, sender: str, payload: Any, priority: int = 0) -> None:
        """Publishes a message to the bus."""
        self._logger.debug("Published Message")
        message = Message(
            topic=topic,
            timestamp=time.time(),
            sender=sender,
            payload=payload,
            priority=priority
        )
        self._queue.put(message)
    
    def subscribe(self, topic: str, callback: Callable) -> None:
        """Subscribes a callback to a topic."""
        with self._lock:
            if topic not in self._handlers:
                self._handlers[topic] = []
            self._handlers[topic].append(callback)
            self._logger.info("New Subscriber")

    def on(self, topic: str, callback: Callable) -> None:
        """Alias for subscribe."""
        self.subscribe(topic, callback)
    
    def start(self, main_thread: threading.Thread) -> None:
        """Starts the message bus thread."""
        if self._thread and self._thread.is_alive():
            return
        self._register_thread("MAIN", main_thread)
        self._running = True
        self._thread = threading.Thread(target=self._process_messages, daemon=True)
        self._thread.start()
        self._logger.info("Started Thread")
    
    def stop(self) -> None:
        """Stops the message bus."""
        self._running = False
        self._logger.info("Stopping Thread...")
        self._queue.put(None)  # Wake up the worker thread if it's blocked on get()
        if self._thread:
            self._thread.join()
    
    def _execute_in_thread(self, message: Message, affinity: str) -> None:
        """
        Execute a message handler in the affinity thread.
        
        Args:
            message: The message to process
            affinity: The thread name where handler should execute
        """
        thread = self._thread_registry.get(affinity)
        if thread:
            with self._lock:
                if affinity not in self._affinity_queues:
                    self._affinity_queues[affinity] = Queue()
            
            self._affinity_queues[affinity].put(message)
            
            # Signal the thread to process
            with self._lock:
                thread.event = threading.Event()
                thread.event.set()
            
            # Wait for thread to complete (with timeout)
            thread.join(timeout=0.5)
    
    def _process_messages(self) -> None:
        """Processes messages from the queue."""
        while self._running:
            try:
                message = self._queue.get()
                if message is None:
                    break
                
                # Get handlers for this specific topic and global handlers
                with self._lock:
                    topic_handlers = self._handlers.get(message.topic, [])[:]
                    global_handlers = self._handlers.get("all", [])[:]
                
                for handler in topic_handlers + global_handlers:
                    try:
                        # Check thread affinity for handler
                        affinity = self._thread_affinity.get(handler.__name__)
                        if affinity:
                            # Execute in the affinity thread
                            self._execute_in_thread(message, affinity)
                        else:
                            # Execute directly in bus thread
                            handler(message.payload)
                    except Exception:
                        pass
                
                self._queue.task_done()
            except Exception:
                break
        
        # Process affinity queues
        for affinity, queue in self._affinity_queues.items():
            while queue.qsize() > 0:
                try:
                    message = queue.get()
                    if affinity in self._thread_registry:
                        thread = self._thread_registry[affinity]
                        with self._lock:
                            thread.event.set()
                        thread.join(timeout=1)
                        thread.event.clear()
                except Exception:
                    break
    
    def is_running(self) -> bool:
        """Checks if the message bus is running."""
        return self._running
    
    def get_stats(self) -> Dict[str, Any]:
        """Gets statistics about the message bus."""
        return {
            "queue_size": self._queue.qsize(),
            "handlers_count": len(self._handlers),
            "running": self._running
        }
    
    def wait_for_shutdown(self) -> None:
        """
        Block until app.shutdown is published.
        
        Used by main.py to keep the application running until shutdown is requested.
        """
        self._shutdown_event.wait()
    
    def _register_thread(self, thread_name: str, thread: Optional[threading.Thread] = None) -> threading.Thread:
        """
        Register a thread with the bus.
        
        Args:
            thread_name: Unique name for the thread
            thread: Thread object (if None, creates a new one)
        
        Returns:
            The registered thread
        """
        with self._thread_registry_lock:
            if thread_name in self._thread_registry:
                self._logger.warning(f"Thread '{thread_name}' already registered")
                return self._thread_registry[thread_name]
            
            if thread is None:
                thread = threading.Thread(name=thread_name, daemon=True)
            
            self._thread_registry[thread_name] = thread
            self._logger.debug(f"Registered thread: {thread_name}")
            return thread
    
    def get_thread(self, thread_name: str) -> Optional[threading.Thread]:
        """Get a registered thread by name."""
        with self._thread_registry_lock:
            return self._thread_registry.get(thread_name)
    
    def get_all_threads(self) -> Dict[str, threading.Thread]:
        """Get all registered threads."""
        with self._thread_registry_lock:
            return dict(self._thread_registry)
    
    def get_current_thread_name(self) -> Optional[str]:
        """Get the name of the current thread if it's registered."""
        current = threading.current_thread()
        with self._thread_registry_lock:
            for name, thread in self._thread_registry.items():
                if thread == current:
                    return name
        return None
    
    def register_thread_affinity(self, thread_name: str, affinity: str) -> None:
        """
        Register thread affinity for a component.
        
        Args:
            thread_name: Name of the thread
            affinity: Thread name this component should run in
        """
        with self._thread_registry_lock:
            self._thread_affinity[thread_name] = affinity
            self._logger.debug(f"Registered thread affinity: {thread_name} -> {affinity}")
    
    def get_thread_affinity(self, handler_name: str) -> Optional[str]:
        """
        Get the thread affinity for a handler.
        
        Args:
            handler_name: Name of the handler
            
            Returns:
                Thread name where handler should execute
        """
        return self._thread_affinity.get(handler_name)
    
    def _on_shutdown(self, payload: Any) -> None:
        """Handle shutdown event."""
        self._logger.info("Shutdown event received")
        self._shutdown_event.set()
        self.stop()