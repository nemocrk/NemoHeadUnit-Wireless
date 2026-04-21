# Component Registry Pattern Implementation

## Overview

The application now uses a **Component Registry** pattern for component initialization. This approach:

1. ✅ Eliminates direct instantiation in main.py
2. ✅ Allows components to control their own registration logic
3. ✅ Makes main.py a true bootstrap orchestrator
4. ✅ Maintains full bus-driven architecture for runtime behavior

---

## Architecture

### Before: Direct Orchestration

```python
# main.py did all the work
class Main:
    def _on_initialize(self, payload):
        self._connection = ConnectionManager(bus)      # Direct creation
        self._connection.on()                          # Direct method call
        self._wireless = WirelessApp(bus)              # Direct creation
        self._wireless.start()                         # Direct method call
```

### After: Component Registry

```python
# Components register themselves
class ComponentRegistry(ABC):
    @abstractmethod
    def register(self, message_bus: MessageBus) -> bool:
        """Component registers with bus and initializes itself"""
        pass

# main.py only orchestrates
class Application:
    def register_components(self):
        self._connection.register(self._bus)  # Component controls init
        self._wireless.register(self._bus)    # Component controls init
        self._gui.register(self._bus, ...)    # Component controls init
```

---

## Component Registry Pattern

### Abstract Base Class

```python
from abc import ABC, abstractmethod
from app.message_bus import MessageBus

class ComponentRegistry(ABC):
    """All registrable components inherit from this"""
    
    @abstractmethod
    def register(self, message_bus: MessageBus) -> bool:
        """
        Register component and initialize.
        
        Returns:
            True if successful, False otherwise
        """
        pass
    
    @abstractmethod
    def name(self) -> str:
        """Get component name for logging"""
        pass
```

### Implementing Components

#### Example: ConnectionManager

```python
class ConnectionManager(BaseInterface, ComponentRegistry):
    def __init__(self):
        # No bus reference - components are independent
        self._bus: Optional[MessageBus] = None
        self._logger = LoggerManager.get_logger('app.connection')
    
    def register(self, message_bus: MessageBus) -> bool:
        """Register with bus and start"""
        self._bus = message_bus
        self._logger.info("ConnectionManager registering")
        
        # Subscribe to events
        self._bus.subscribe('wireless.app.ready', self._on_wireless_ready)
        
        # Initialize component
        return self.on()
    
    def name(self) -> str:
        return "connection_manager"
```

#### Example: WirelessApp

```python
class WirelessApp(ComponentRegistry):
    def __init__(self):
        # No bus reference
        self._bus: Optional[MessageBus] = None
        self._logger = LoggerManager.get_logger('app.wireless')
    
    def register(self, message_bus: MessageBus) -> bool:
        """Register with bus"""
        self._bus = message_bus
        self._logger.info("WirelessApp registering")
        
        # Subscribe to events
        self._bus.subscribe('connection.ready', self._on_connection_ready)
        
        return True
    
    def name(self) -> str:
        return "wireless_app"
```

#### Example: GUIComponent

```python
class GUIComponent(ComponentRegistry):
    def __init__(self):
        self._bus: Optional[MessageBus] = None
        self._connection_manager: Optional[ConnectionManager] = None
    
    def register(self, message_bus: MessageBus, 
                 connection_manager: ConnectionManager) -> bool:
        """Register with bus and store dependencies"""
        self._bus = message_bus
        self._connection_manager = connection_manager
        
        # Subscribe to events
        self._bus.subscribe('gui.startup.requested', self._on_startup)
        
        return True
    
    def name(self) -> str:
        return "gui_component"
```

---

## main.py Simplified

### New main.py (Super Simple!)

```python
class Application:
    def __init__(self):
        self._bus = MessageBus()
        
        # Create all components (no registration yet)
        self._connection = ConnectionManager()
        self._wireless = WirelessApp()
        self._gui = GUIComponent()
    
    def register_components(self) -> bool:
        """Each component registers itself"""
        self._connection.register(self._bus)
        self._wireless.register(self._bus)
        self._gui.register(self._bus, self._connection)
        return True
    
    def run(self) -> int:
        if not self.register_components():
            return 1
        
        self._bus.start()
        self._bus.publish('gui.startup.requested', 'app.main', {})
        
        # Block until shutdown
        self._bus.wait_for_shutdown()
        
        return 0
```

### What This Gives Us

1. **main.py is now truly minimal** - Just creates, registers, and starts
2. **Components are independent** - No bus reference in __init__
3. **Clear initialization phases**:
   - Phase 1: Create components (no side effects)
   - Phase 2: Register components (set up bus subscriptions)
   - Phase 3: Start bus (event processing begins)
   - Phase 4: Request startup (first event published)

---

## Event Flow with Registry Pattern

### Startup Sequence

```
1. Application() creates all components
   ├─ ConnectionManager()
   ├─ WirelessApp()
   └─ GUIComponent()

2. register_components() 
   ├─ connection.register(bus)
   │  └─ Subscribes to wireless.app.ready
   ├─ wireless.register(bus)
   │  └─ Subscribes to connection.ready
   └─ gui.register(bus, connection)
      └─ Subscribes to gui.startup.requested

3. bus.start()
   └─ Message processing begins

4. bus.publish('gui.startup.requested', ...)
   └─ gui._on_startup() called
      └─ GUI window created and shown
```

### Runtime Communication

```
All runtime events still flow through bus:

gui.pairing_clicked() 
  → bus.publish('connection.pairing.requested', ...)
  → connection._on_pairing_requested()
  → bus.publish('wireless.bluetooth.discovery.started', ...)
  → gui._on_discovery_status()
  → Update display
```

---

## Key Benefits

### 1. Separation of Concerns
- **Creation** (in __init__) is separate from **initialization** (in register)
- Components don't need bus until registration
- Easy to test with mock buses

### 2. Clear Dependency Management
```python
# Dependencies are explicit
gui.register(bus, connection_manager)  # ✓ Clear what GUI needs
```

### 3. Easy to Add Components
```python
# Just create new component and call register
new_component = MyComponent()
new_component.register(self._bus, other_components...)
```

### 4. Testability
```python
# Can create components without bus
component = MyComponent()

# Test initialization
assert component.register(mock_bus) == True

# Verify subscriptions
assert mock_bus.subscribe.called
```

### 5. Graceful Degradation
```python
# If registration fails, caught early
if not component.register(self._bus):
    logger.error("Component registration failed")
    return False  # Clean exit
```

---

## Comparison: Before vs After

| Aspect | Before | After |
|--------|--------|-------|
| **main.py lines** | 100+ | ~40 |
| **Direct instantiation** | In main | In Application.__init__ |
| **Bus reference in __init__** | Yes | No |
| **Component initialization** | main calls on() | Component calls on() in register() |
| **Dependency tracking** | main holds refs | Application holds refs |
| **Adding new components** | Modify main.py | Create component, add register() call |
| **Testing components** | Requires mock bus | Can test without bus |

---

## Migration Path

If you have existing components:

### Step 1: Inherit from ComponentRegistry
```python
class MyComponent(ComponentRegistry):
    pass
```

### Step 2: Move bus initialization to register()
```python
def __init__(self):
    # Remove: self._bus = message_bus or MessageBus()
    self._bus: Optional[MessageBus] = None

def register(self, message_bus: MessageBus) -> bool:
    self._bus = message_bus
    # Move all bus-related initialization here
    return True
```

### Step 3: Update main.py to call register()
```python
component = MyComponent()
component.register(self._bus)
```

---

## Future Enhancements

### Automatic Component Discovery
```python
# Could automatically find all ComponentRegistry subclasses
import inspect
for name, obj in inspect.getmembers(app):
    if isinstance(obj, type) and issubclass(obj, ComponentRegistry):
        instance = obj()
        instance.register(self._bus)
```

### Dependency Injection
```python
# Could validate dependencies before registration
required_deps = {'connection': ConnectionManager}
for dep_name, dep_type in required_deps.items():
    if not isinstance(getattr(self, f'_{dep_name}'), dep_type):
        raise ValueError(f"Missing dependency: {dep_name}")
```

### Component Lifecycle Hooks
```python
class ComponentRegistry:
    def on_registered(self): pass      # Called after register()
    def on_bus_started(self): pass     # Called after bus.start()
    def on_shutdown(self): pass        # Called on app.shutdown
```

---

## Summary

The **Component Registry Pattern** transforms main.py from an orchestrator that does all the work into a simple bootstrap that just:

1. Creates components
2. Registers components 
3. Starts the bus
4. Publishes the first event
5. Waits for shutdown

All component-specific logic moves into the component's `register()` method, making the codebase more modular and maintainable.

---

**Pattern:** Service Locator / Component Registry  
**Status:** ✅ Implemented  
**Files:** `app/component_registry.py` (base class), all components
