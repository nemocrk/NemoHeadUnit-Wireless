# Bus-Driven Architecture Guide

## Quick Reference for Developers

This guide explains how to work with the NemoHeadUnit-Wireless bus-driven architecture.

---

## Core Concepts

### The Message Bus
The `MessageBus` is a singleton that routes all communication between components.

```python
from app.message_bus import MessageBus

# Get the singleton instance
bus = MessageBus()

# Or pass it as parameter
def __init__(self, message_bus: Optional[MessageBus] = None):
    self._bus = message_bus or MessageBus()
```

### Topics
Communication is organized by **topics** - hierarchical namespaces:
- `app.*` - Application lifecycle
- `connection.*` - Connection management
- `wireless.*` - Wireless subsystem
- `gui.*` - GUI components
- `media.*` - Media streaming
- `config.*` - Configuration

---

## Publishing Events

### Basic Publishing
```python
self._bus.publish(
    topic="connection.status",      # Topic name
    sender="connection_manager",    # Component identifier
    payload={"status": "connected"} # Event data
)
```

### With Priority
```python
self._bus.publish(
    topic="app.initialize",
    sender="app.main",
    payload={},
    priority=1  # Higher priority processes first
)
```

---

## Subscribing to Events

### Subscribe to Specific Topic
```python
def __init__(self):
    self._bus = MessageBus()
    self._bus.subscribe("connection.status", self._on_connection_changed)

def _on_connection_changed(self, payload):
    """Receives only connection.status events"""
    status = payload.get("status")
    print(f"Connection status: {status}")
```

### Subscribe to All Events
```python
self._bus.register_handler(self._on_any_event)

def _on_any_event(self, message):
    """Receives all messages"""
    print(f"Event: {message.topic} from {message.sender}")
```

### Alias: `on()` Method
```python
# These are equivalent:
self._bus.subscribe("topic", handler)
self._bus.on("topic", handler)
```

---

## Logging

### Using LoggerManager
```python
from app.logger import LoggerManager

# Get logger for your module
logger = LoggerManager.get_logger('app.my_module')

# Use standard Python logging methods
logger.debug("Debug message")
logger.info("Info message")
logger.warning("Warning message")
logger.error("Error message")
logger.critical("Critical message")
```

### Per-Module Verbosity
```python
# Set specific module to DEBUG
LoggerManager.set_verbosity('app.wireless.main', logging.DEBUG)

# Set all modules to INFO
LoggerManager.set_all_verbosity(logging.INFO)

# Get current verbosity
level = LoggerManager.get_verbosity('app.connection')
```

---

## Component Lifecycle Pattern

### Proper Component Structure
```python
from app.base_interface import BaseInterface
from app.message_bus import MessageBus
from app.logger import LoggerManager

class MyComponent(BaseInterface):
    def __init__(self, message_bus: Optional[MessageBus] = None):
        self._bus = message_bus or MessageBus()
        self._logger = LoggerManager.get_logger('app.my_component')
        
        # Subscribe to initialization event
        self._bus.subscribe('app.initialize', self._on_app_initialize)
        
        # Subscribe to other relevant events
        self._bus.subscribe('connection.ready', self._on_connection_ready)
    
    @property
    def name(self) -> str:
        return "my_component"
    
    @property
    def status(self) -> str:
        return "running" if self.is_running() else "stopped"
    
    def on(self) -> bool:
        """Start the component"""
        self._logger.info(f"Starting {self.name}")
        # Do initialization
        self._bus.publish('my_component.ready', self.name, {})
        return True
    
    def off(self) -> bool:
        """Stop the component"""
        self._logger.info(f"Stopping {self.name}")
        # Do cleanup
        self._bus.publish('my_component.stopped', self.name, {})
        return True
    
    def is_running(self) -> bool:
        """Check if running"""
        return True  # Implement actual check
    
    def get_status(self) -> dict:
        """Get component status"""
        return {"status": self.status}
    
    def reset(self) -> None:
        """Reset component"""
        pass
    
    def log(self, message: str, level: str = "info") -> None:
        """Log a message"""
        self._logger.info(message)
    
    # Event handlers
    def _on_app_initialize(self, payload):
        """Handle app initialization"""
        self._logger.debug("App initializing")
    
    def _on_connection_ready(self, payload):
        """Handle connection ready"""
        self.on()
```

---

## Common Event Flow Patterns

### 1. Initialization Sequence
```python
# 1. App starts bus and publishes initialize
app.on()
bus.publish('app.initialize', 'app.main', {})

# 2. Each component subscribes and reacts
ConnectionManager._on_initialize()
bus.publish('connection.ready', 'connection_manager', {})

# 3. Dependent components react
WirelessApp._on_connection_ready()
bus.publish('wireless.app.ready', 'wireless_app', {})
```

### 2. User Action Flow
```
User clicks button → GUI publishes event
GUI.pairing_button_clicked()
bus.publish('connection.pairing.requested', 'gui.connection_tab', {...})

Wireless component receives and acts
WirelessApp._on_pairing_requested()
bus.publish('wireless.bluetooth.discovery.started', 'wireless_app', {})

GUI receives status updates
GUI._on_discovery_status()
# Update display
```

### 3. Configuration Change Flow
```
User changes config → GUI publishes event
bus.publish('config.settings', 'gui.config_tab', {setting: value})

All components receive and apply
Component._on_config_changed()
# Apply setting
bus.publish('component.config.applied', 'component', {})
```

---

## Best Practices

### ✅ DO

1. **Always pass MessageBus as parameter**
   ```python
   def __init__(self, message_bus: Optional[MessageBus] = None):
       self._bus = message_bus or MessageBus()
   ```

2. **Use LoggerManager for logging**
   ```python
   logger = LoggerManager.get_logger('app.module')
   ```

3. **Subscribe in `__init__`**
   ```python
   self._bus.subscribe('topic', self._handler)
   ```

4. **Publish state changes**
   ```python
   self._bus.publish('module.state.changed', self.name, {state: new_state})
   ```

5. **Use hierarchical topic names**
   ```python
   'connection.pairing.requested'  # ✅ Good
   'pairing_requested'              # ❌ Bad
   ```

### ❌ DON'T

1. **Don't create components directly**
   ```python
   # ❌ Wrong
   def __init__(self):
       self.wireless = WirelessApp()
   
   # ✅ Correct
   def __init__(self):
       self._bus.subscribe('connection.ready', self._on_ready)
   ```

2. **Don't call methods directly for lifecycle**
   ```python
   # ❌ Wrong
   self.wireless.start()
   
   # ✅ Correct
   self._bus.publish('wireless.start.requested', self.name, {})
   ```

3. **Don't use print() for logging**
   ```python
   # ❌ Wrong
   print("Error occurred")
   
   # ✅ Correct
   logger.error("Error occurred")
   ```

4. **Don't share MessageBus between threads unsafely**
   ```python
   # The bus is thread-safe internally
   # Just use it normally
   ```

---

## Debugging Tips

### View All Bus Events
```python
# Subscribe to all events
bus.register_handler(lambda message: print(f"{message.topic}: {message.payload}"))
```

### Enable Debug Logging
```python
from app.logger import LoggerManager
import logging

# Debug all modules
LoggerManager.set_all_verbosity(logging.DEBUG)

# Debug specific module
LoggerManager.set_verbosity('app.wireless.main', logging.DEBUG)
```

### Check Bus Statistics
```python
stats = bus.get_stats()
print(f"Queue size: {stats['queue_size']}")
print(f"Active handlers: {stats['handlers_count']}")
print(f"Bus running: {stats['running']}")
```

---

## Extending the Architecture

### Adding a New Component

1. **Create the component class**
   ```python
   class MyNewComponent(BaseInterface):
       def __init__(self, message_bus: Optional[MessageBus] = None):
           self._bus = message_bus or MessageBus()
           self._logger = LoggerManager.get_logger('app.my_new_component')
   ```

2. **Subscribe to relevant events**
   ```python
       # Listen for triggers
       self._bus.subscribe('my_component.start', self._on_start)
   ```

3. **Publish state changes**
   ```python
       def on(self) -> bool:
           # Initialize
           self._bus.publish('my_component.ready', self.name, {})
   ```

4. **Integrate with main app**
   ```python
   # In app/main.py
   def _on_initialize(self, payload):
       self._my_component = MyNewComponent(self._bus)
       self._my_component.on()
   ```

### Adding a New Event Topic

1. **Document in module docstring**
   ```python
   """
   Event Topics:
   - my_module.event.type: Description
   """
   ```

2. **Use in publish**
   ```python
   self._bus.publish('my_module.event.type', self.name, payload)
   ```

3. **Subscribe where needed**
   ```python
   self._bus.subscribe('my_module.event.type', self._handler)
   ```

---

## Performance Considerations

### Message Queue Depth
- Monitor with `bus.get_stats()['queue_size']`
- Large queue may indicate slow handlers
- Consider async processing for long operations

### Handler Performance
- Keep handlers quick - return immediately
- Spawn threads for long operations
- Publish completion events when done

### Thread Safety
- Bus is fully thread-safe
- Handlers run in bus thread
- Access shared state from handler safely

---

## References

- **Project Vision:** `/docs/project-vision.md`
- **Compatibility Report:** `/docs/COMPATIBILITY_REPORT.md`
- **Base Interface:** `app/base_interface.py`
- **Message Bus:** `app/message_bus.py`
- **Logger:** `app/logger.py`

---

**Last Updated:** 2026-04-20  
**Version:** 1.0
