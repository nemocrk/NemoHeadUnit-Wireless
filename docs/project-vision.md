# Project Vision: NemoHeadUnit-Wireless

## Executive Summary

NemoHeadUnit-Wireless is a full-stack application designed to emulate an Android Auto head unit experience on edge devices. The system enables remote control and configuration of Android Auto functionality through wireless connections, providing a powerful tool for drivers and developers alike.

## Vision Statement

**To create a high-performance, Python-based emulation platform that brings the full Android Auto experience to edge devices, enabling seamless wireless connectivity, real-time media streaming, and comprehensive head unit configuration—all without requiring C++ development.**

**To build a modular, not-giant-blobs architecture that avoids monolithic code structures, ensuring maintainability, scalability, and separation of concerns.**

---

## 1. Primary Purpose

The primary purpose of NemoHeadUnit-Wireless is to emulate an Android Auto head unit, allowing users to:
- Control and configure Android Auto functionality remotely
- Stream video and audio channels from the smartphone
- Route microphone audio to the head unit when requested
- Provide a touch-friendly user interface for interaction

---

## 2. Target Users

**Primary Audience:**
- Drivers who use Android Auto while operating vehicles
- Automotive technology enthusiasts
- Developers and testers of Android Auto applications
- System integrators working with embedded automotive platforms

**User Needs:**
- Safe driving experience with hands-free control
- Remote monitoring and configuration of Android Auto
- Touch-based interaction without physical device access

---

## 3. Core Features

### 3.1 Connection Management
- **Remote Connection**: Bluetooth and WiFi AP protocols for smartphone connectivity
- **Auto-Pairing**: Automated Bluetooth pairing methods
- **Wireless Protocols**: Dual support for Bluetooth and WiFi AP for redundancy

### 3.2 Media Streaming
- **Video Channel**: Projection of Android Auto video streams (H.264 Annex-B)
- **Audio Channel**: Reproduction of Android Auto audio streams (AAC)
- **Microphone Routing**: Dynamic mic routing to head unit when requested by Android Auto
- **Zero-Copy Transport**: Binary frames published directly on the IPC bus without deserialisation

### 3.3 Configuration System
- **Head Unit Configuration**: Full configuration of every aspect of Android Auto
- **Associated Stack**: Complete integration with Android Auto stack elements
- **Touch UI**: Intuitive, touch-mode user interface for interaction

### 3.4 Technical Implementation
- **Python-Only Architecture**: Modern architecture leveraging Python 3.14
- **Async IPC Message Bus**: ZeroMQ XPUB/XSUB broker with msgpack serialisation replaces the in-process event emitter. Every inter-process communication is routed through the bus.
- **Multi-Process Design**: Each major subsystem runs as an independent OS process connected to the central broker.
- **Qt Thread Safety**: All Qt widget callbacks dispatched via `QtBusBridge` (pyqtSignal QueuedConnection) from the ZMQ recv thread to the Qt main thread.

---

## 4. Technical Architecture

### 4.1 Architecture Principles
- **Python-Only**: No C++ dependencies; pure Python implementation
- **Multi-Process IPC**: Independent OS processes communicate exclusively through the ZeroMQ XPUB/XSUB broker (`bus_broker.py`)
- **Message-Bus Centricity**: Every interaction between modules—from instantiation and initialization to runtime media frames—is strictly routed through the bus. No Python object references cross process boundaries.
- **Qt Thread Safety**: The `QtBusBridge` (`app/gui/qt_bridge.py`) ensures ZMQ recv-thread callbacks are always delivered to the Qt main thread via `QueuedConnection`.
- **Logging**: Per-module verbosity control implemented via the Logger module (`app/logger.py`)

---

### 4.2 Process Map

```
┌─────────────────────┐
│   bus_broker.py      │  ZeroMQ XPUB/XSUB — routes all IPC
│   (XSUB :pub)        │  ipc:///tmp/nemobus.pub
│   (XPUB :sub)        │  ipc:///tmp/nemobus.sub
└─────────────────────┘
         │ IPC (msgpack frames)
    ────┴──────────────────────────
    │                          │                     │
┌───┴────────┐   ┌───────────────┐   ┌───────────────┐
│  app/main.py  │   │wireless_daemon│   │media_renderer │
│  GUI Process  │   │   (services/) │   │  (services/)  │
│  PyQt6 +      │   │  Bluetooth +  │   │ GStreamer /   │
│  QtBusBridge  │   │  TCP Server   │   │ ffplay fallbk │
└──────────────┘   └───────────────┘   └───────────────┘
```

### 4.3 Key Source Files

| File | Role |
|------|------|
| `bus_broker.py` | Central ZMQ XPUB/XSUB broker — must start first |
| `app/bus_client.py` | `BusClient` — drop-in replacement for `MessageBus` |
| `app/message_bus.py` | Compatibility shim: `MessageBus = BusClient` |
| `app/gui/qt_bridge.py` | `QtBusBridge` — ZMQ→Qt main thread dispatch |
| `app/main.py` | Starts broker + daemons as subprocesses, runs Qt loop |
| `app/gui/modern_main_window.py` | Main window, all bus callbacks use `thread="main"` |
| `app/connection.py` | `ConnectionManager` — serialisable payloads only |
| `app/component_registry.py` | Abstract base for bus-registered components |
| `services/wireless_daemon.py` | Bluetooth/TCP standalone process |
| `services/media_renderer.py` | AAC/H.264 playback via GStreamer or ffplay |

### 4.4 Message Frame Format

Every IPC message is a ZMQ 3-frame multipart:

```
Frame 0  topic    bytes          routing key (e.g. b"connection.status")
Frame 1  header   msgpack dict   {sender, ts, priority}
Frame 2  payload  bytes          msgpack dict OR raw binary (audio/video)
```

Audio/video frames carry raw AAC or H.264 bytes in Frame 2 for zero-copy delivery.

### 4.5 Performance Requirements
- **Audio latency**: ≤10 ms (50 fps AAC, ~320–640 B/frame)
- **Video latency**: ≤33 ms (30 fps H.264, up to ~400 KB IDR frame)
- **Low CPU**: Optimised for resource-constrained edge devices (Atom)
- **Broker overhead**: Zero Python overhead per message (native C `zmq.proxy`)

### 4.6 Deployment Stack
- **Environment**: Python 3.14 via conda environment (`py314`)
- **Dependencies**: `pyzmq`, `msgpack`, `PyQt6`, `PyGObject` (optional GStreamer)
- **Build System**: Local build and deployment through SSH
- **Package Format**: Bundled deb package
- **Target OS**: Ubuntu 24 (edge device deployment)

---

## 5. Non-Functional Requirements

### 5.1 Performance
- **High Performance**: Optimised for low CPU peripherals
- **IPC Throughput**: ZeroMQ native proxy eliminates Python serialisation overhead per hop
- **Latency**: Minimal latency in media streaming and control

### 5.2 Reliability
- **Stable Connection**: Reliable wireless protocols (Bluetooth/WiFi AP)
- **Failover**: Automatic fallback between connection methods
- **Media Fallback**: GStreamer preferred; ffplay subprocess fallback when GStreamer unavailable
- **Recovery**: Graceful degradation when features fail

### 5.3 Security
- **Secure Pairing**: Encrypted Bluetooth pairing
- **Secure Transmission**: Encrypted WiFi AP communication
- **Authentication**: Secure connection authentication
- **Local IPC only**: ZMQ broker binds to `ipc://` (Unix domain sockets) — not exposed on the network

---

## 6. Deployment Environment

### 6.1 Target Platform
- **Edge Devices**: Embedded systems for automotive use
- **Operating System**: Ubuntu 24
- **Processor**: Optimised for older Atom processors

### 6.2 Deployment Method
- **Local Build**: Application built locally
- **SSH Deployment**: Automated deployment via SSH
- **Package Format**: Bundled deb package for easy installation

### 6.3 Startup Order
1. `bus_broker.py` — bind IPC sockets
2. `services/wireless_daemon.py` — Bluetooth + TCP
3. `services/media_renderer.py` — audio/video playback
4. `app/main.py` — GUI process (starts 1–2 as subprocesses automatically)

---

## 7. Success Metrics

### 7.1 Quality Metrics
- **Test Coverage**: Comprehensive test coverage as primary success indicator
- **Ease of Use**: User-friendly interface and deployment
- **Reliability**: High uptime and connection stability

### 7.2 User Experience
- **Touch Mode**: Intuitive touch interface for drivers
- **Remote Access**: Seamless remote connection
- **Configuration**: Easy-to-use configuration system
- **Technology**: PyQt6

---

## 8. Constraints

### 8.1 Technical Constraints
- **Processor Limitation**: Optimised for older Atom processors
- **Resource Optimisation**: High performance on low CPU peripherals
- **Python-Only**: No C++ dependencies; pure Python implementation

### 8.2 Development Constraints
- **Python 3.14**: Standard Python 3.14 environment via conda
- **No C++**: All functionality in Python only

---

## 9. Nemo Ecosystem

**Current Status**: Not yet integrated with existing Nemo technologies

**Future Alignment**:
- Potential integration with Nemo automotive ecosystem
- Compatibility with Nemo development tools
- Alignment with Nemo security standards

---

## 10. Critical Assumptions

### 10.1 Environment Assumptions
- **Python 3.14**: Standard Python 3.14 environment via conda (`py314`)
- **Ubuntu 24**: Target operating system for edge devices
- **Atom Processor**: Optimised for older Atom processor capabilities
- **ZeroMQ**: `pyzmq` and `msgpack` available in the conda environment

### 10.2 User Behavior Assumptions
- **Driver Safety**: Users prioritise hands-free operation
- **Wireless Preference**: Preference for wireless over wired connections
- **Touch Interaction**: Users expect touch-based interface

### 10.3 Technology Adoption
- **Python Adoption**: Full Python ecosystem adoption
- **Edge Computing**: Growing adoption of edge computing in automotive
- **Android Auto**: Continued growth of Android Auto adoption

---

## 11. Technical Stack Summary

| Component | Technology |
|-----------|------------|
| Language | Python 3.14 |
| Environment | Conda (`py314`) |
| OS | Ubuntu 24 |
| Processor | Atom (older generation) |
| Deployment | deb package via SSH |
| Connection | Bluetooth + WiFi AP |
| IPC Broker | ZeroMQ XPUB/XSUB (`pyzmq`) |
| Serialisation | msgpack |
| GUI Framework | PyQt6 |
| Qt Thread Bridge | `QtBusBridge` (pyqtSignal QueuedConnection) |
| Media Playback | GStreamer (primary) / ffplay (fallback) |
| Logging | Per-module verbosity control (`app/logger.py`) |

---

## 12. Vision Timeline

**Phase 1: Foundation** ✅ *completed*
- Core Python architecture implementation
- ZeroMQ XPUB/XSUB message bus (replaces in-process emitter)
- Basic connection protocols

**Phase 2: Core Features**
- Video/audio streaming (H.264 + AAC via IPC binary frames)
- Remote configuration
- Touch UI development

**Phase 3: Optimisation**
- Performance optimisation for Atom processors
- Test coverage expansion
- Reliability improvements

**Phase 4: Deployment**
- Edge device deployment
- Production deployment via SSH
- Comprehensive testing

---

## 13. Key Differentiators

1. **Pure Python**: No C++ dependencies; accessible to Python developers
2. **Async IPC Bus**: ZeroMQ XPUB/XSUB replaces the in-process event emitter; processes are fully isolated and can be restarted independently
3. **Zero-Copy Media**: Audio/video frames travel as binary ZMQ frames—never deserialised by the broker
4. **Qt Thread Safety**: `QtBusBridge` guarantees all widget operations run on the Qt main thread
5. **Edge Optimisation**: Optimised for older Atom processors
6. **Modular Logging**: Per-module verbosity control for debugging and troubleshooting

---

## 14. Future Considerations

- **Nemo Integration**: Potential alignment with Nemo ecosystem
- **Android Auto Updates**: Compatibility with future Android Auto versions
- **Security Enhancements**: Enhanced security protocols
- **Performance Improvements**: Continuous optimisation for edge devices
- **TCP Transport**: Optional switch from `ipc://` to `tcp://` for multi-host deployments

---

## 15. v2 Modular Implementation Guidelines

### 15.1 Purpose of v2

The `v2/` folder contains the next-generation implementation of NemoHeadUnit-Wireless. Its purpose is to preserve the same product goal of Android Auto head unit emulation while enforcing a stricter modular architecture based entirely on isolated standalone processes.

The v2 architecture exists to make the system easier to extend, easier to reason about, and safer to evolve over time without creating new monolithic code paths.

### 15.2 Technology Direction for v2

- **Python-Only**: All code in `v2/` must remain pure Python
- **ZeroMQ IPC**: All inter-module communication must go through the dedicated v2 bus broker
- **Standalone Processes**: Every module runs as its own OS process
- **No Direct Coupling**: Modules must not import, instantiate, or call each other directly
- **Autodiscovery**: The main orchestrator discovers modules dynamically from the filesystem
- **Graceful Lifecycle**: The main orchestrator is responsible for start and stop coordination

### 15.3 v2 Folder Structure

The expected structure of `v2/` is:

```
v2/
├── main.py
├── bus_broker.py
├── shared/
│   └── bus_client.py
└── modules/
    └── <module_name>/
        └── main.py
```

### 15.4 Responsibilities of `v2/main.py`

The `v2/main.py` file is the orchestrator of the whole v2 runtime. It must:

- Start the v2 bus broker before any module
- Discover modules dynamically by scanning `v2/modules/*/main.py`
- Launch every discovered module as an independent subprocess
- Publish `system.start` once the runtime is ready
- Publish `system.stop` when the application is shutting down
- Handle `Ctrl+C` and process shutdown in a graceful and deterministic way

The orchestrator must not contain business logic belonging to individual modules.

### 15.5 Responsibilities of each module

Every folder under `v2/modules/` is a standalone module. Each module must:

- Have its own dedicated `main.py`
- Be executable on its own as an independent process
- Connect to the v2 communication bus using the shared bus client
- Receive events from the bus
- Publish events and data back onto the bus
- Keep its business logic internal to the module boundary

A module must be understandable and maintainable in isolation.

### 15.6 Hard rules for module development

The following rules are mandatory for all code under `v2/modules/`:

- No direct imports between sibling modules
- No shared runtime state between modules except through the bus
- No hidden dependencies on startup order between modules beyond the broker-first rule
- No module-specific logic inside the orchestrator except process launch concerns
- No giant central files that absorb behavior from multiple modules
- No bypass of the communication bus for control, state, or media exchange

If one module needs information from another, it must subscribe to a topic on the bus and react to the published message.

### 15.7 Shared code in v2

Shared code is allowed only inside `v2/shared/` and must stay generic.

Allowed examples:
- Bus client helpers
- Shared serialization helpers
- Shared message envelope helpers
- Generic process utilities

Not allowed examples:
- Business rules for a specific module
- Cross-module orchestration logic hidden inside shared helpers
- Module-specific state stored in shared components

### 15.8 Module contract

Each module should be designed around a clear contract:

- **Inputs**: subscribed bus topics
- **Outputs**: published bus topics
- **Lifecycle**: reaction to `system.start` and `system.stop` when applicable
- **State**: private to the module unless explicitly published

This contract should be documented close to the module implementation as the module is developed.

### 15.9 Development workflow for v2 modules

When implementing a new module in `v2/`, developers should follow this order:

1. Define the purpose of the module
2. Define which topics it subscribes to
3. Define which topics it publishes
4. Implement its standalone `main.py`
5. Keep all internal logic inside the module folder
6. Verify it can run independently on the bus
7. Verify it starts correctly when auto-discovered by `v2/main.py`

### 15.10 Naming and modularity goals

- Folder names should reflect the role of the module
- Module internals should remain cohesive and small
- Event handlers should use consistent snake_case naming
- Modules should prefer explicit message contracts over implicit coupling
- The system should be able to grow by adding folders, not by expanding central orchestration code

The main architectural success criterion for `v2/` is simple: adding a new capability should usually mean adding a new module, not making the core runtime more entangled.

---

## Conclusion

NemoHeadUnit-Wireless represents a significant advancement in Android Auto emulation technology. The adoption of ZeroMQ XPUB/XSUB as the central IPC broker enables true process isolation, zero-copy media transport, and clean separation between the GUI, wireless, and media subsystems—all without leaving the Python ecosystem.

**Success is measured by comprehensive test coverage and exceptional ease of use.**

---

*Document Version: 3.1*
*Last Updated: 2026-04-21*
*Author: Nemo Development Team*
