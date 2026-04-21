# Bus-Driven Module Lifecycle

## Overview
In the NemoHeadUnit-Wireless architecture, the Internal Message Bus is not just a data transport; it is the primary mechanism for system orchestration. To ensure total modular isolation and high parallelism, **no module may be directly instantiated or started by another module.**

## The Pattern

### 1. The Bootstrap Phase
The application starts with a minimal "Main" process that initializes only the `MessageBus` and a `ModuleManager`. 

### 2. Module Instantiation (The "Spawn" Request)
When a module needs to be created, a message is published to the bus:
* **Topic:** `system.module.request_spawn`
* **Payload:** `{ "class_name": "ConnectivityModule", "id": "connectivity_primary" }`

### 3. Orchestration
The `ModuleManager` (the only component with a Factory role) performs the following:
1.  **Dynamic Import:** Loads the requested class.
2.  **Thread Allocation:** Creates a new `QThread`.
3.  **Sandboxing:** Instantiates the module and uses `moveToThread()` to isolate its execution.
4.  **Wiring:** Connects the module's signals/slots to the `MessageBus`.

### 4. Startup Handshake
Once the thread starts, the module performs its internal setup and must signal its availability:
* **Topic:** `module.<id>.ready`
* **Payload:** `{ "status": "online" }`

Other modules (like the UI) subscribe to these `ready` messages to update their state, rather than checking if an object exists.

## Key Constraints
- **No Direct Imports:** Modules should not import each other for the purpose of instantiation.
- **Thread Isolation:** Every module should ideally reside in its own thread to prevent UI blocking, managed via the `ModuleManager`.
- **State Invisibility:** A module's internal state is private. Information is shared only via published messages.

## Benefits

| Benefit | Description |
| :--- | :--- |
| **Total Decoupling** | Modules can be replaced or updated without touching the rest of the codebase. |
| **Testability** | Any module can be tested in total isolation by spoofing the bus messages it expects. |
| **Parallelism** | By enforcing thread-per-module instantiation via the Manager, we utilize multi-core processors effectively even with the Python GIL (standard Python 3.14). |
| **Observability** | Every lifecycle event (spawn, ready, crash) is a message that can be logged globally for debugging. |

## Implementation Reference
For implementation examples in Python/PyQt6, refer to the `app/core/module_manager.py` (planned) and the `MessageBus` implementation in `app/message_bus.py`.