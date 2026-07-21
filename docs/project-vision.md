# Project Vision: NemoHeadUnit-Wireless

## Executive Summary

NemoHeadUnit-Wireless is a modular, high-performance platform designed to emulate an Android Auto head unit experience on edge devices. By leveraging process isolation, ZeroMQ-based IPC, and an event-driven design, the system provides a robust and customizable interface for drivers, automotive enthusiasts, and developers.

---

## 1. Vision Statement

**To build a modular, high-performance, Python-based emulation platform that brings the full Android Auto experience to edge devices, enabling seamless wireless connectivity, real-time media streaming, and comprehensive configuration—achieved through pure Python, process isolation, and a robust IPC bus architecture.**

---

## 2. Target Users & Needs

- **Drivers**: Require a safe, hands-free in-car experience with physical/touch navigation controls.
- **Automotive Enthusiasts**: Require an easily deployable headunit image for DIY hardware setups (e.g., Raspberry Pi).
- **Developers & Testers**: Require remote monitoring, payload tracing, and simulation of Android Auto protocol channels without compiler or toolchain friction.

---

## 3. Core Features

### 3.1 Connection Management
- **Initial Handshake**: Bluetooth RFCOMM channel negotiates startup.
- **Wi-Fi Association**: The phone automatically switches to the headunit's hosted 5GHz Access Point (`hostapd` / `dnsmasq`).
- **Control Sockets**: Established over a standard TCP connection (port `5277`) on the local network link.

### 3.2 Media Streaming
- **Video Projection**: H.264 video streams decoded via hardware accelerators (V4L2, NVDEC, etc.) or software fallbacks.
- **Audio Channels**: Distinct media (AAC 48kHz) and navigation/speech guidance (PCM 16kHz) streams routed to the vehicle audio system.
- **Zero-Copy Transport**: High-throughput media streams travel via raw binary frames across the IPC bus to minimize CPU usage.

### 3.3 Dynamic User Interface
- **Window Composition**: Frameless transparent PyQt6 windows coordinate overlay layers to construct a cohesive screen interface.
- **Scandinavian Minimalist Design**: A high-contrast dark theme (warm-white on charcoal) designed specifically for vehicle dashboard readability.

---

## 4. Technical Architecture Principles

- **Multi-Process Isolation**: Subsystems are fully isolated OS processes managed by the orchestrator (`main.py`). A crash in a UI widget or connection manager will not compromise other active services.
- **ZeroMQ Message Bus**: Every interaction between modules, including telemetry and control flags, is routed through the central broker (`bus_broker.py`).
- **Telemetry Tracing**: Messages carry `_trace` headers containing source, topic, sequence numbers, and timestamps to track latency and packet drops.
- **Strict Decoupling**: Modules never directly import or interact with sibling modules. They communicate solely through event subscriptions.

```
┌────────────────────────────────────────────────────────┐
│                      bus_broker.py                     │
│  ZeroMQ XSUB (ipc:///tmp/nemobus_v2.pub)               │
│  ZeroMQ XPUB (ipc:///tmp/nemobus_v2.sub)               │
└──────────────────────────┬─────────────────────────────┘
                           │ IPC (2-Frame JSON)
        ┌──────────────────┼──────────────────┐
        │                  │                  │
┌───────┴──────┐   ┌───────┴──────┐   ┌───────┴──────┐
│   main.py    │   │  bluetooth_  │   │   video_ui   │
│ Orchestrator │   │   manager    │   │  (Widget)    │
└──────────────┘   └──────────────┘   └──────────────┘
```

---

## 5. Technical Stack Summary

| Component | Technology |
|---|---|
| **Language** | Python 3.14 |
| **Operating System** | Ubuntu 24 (Target Edge Platform) |
| **IPC Broker** | ZeroMQ XPUB/XSUB (`pyzmq` over Unix domain sockets) |
| **Serialization** | JSON (Payloads) & Raw Binary (Media Frames) |
| **GUI Framework** | PyQt6 |
| **UI Event Routing** | Global Input Trap Overlay (`ui_shell`) |
| **Media Engine** | GStreamer (Primary) / ffmpeg-ffplay (Fallback) |
| **Audio Server** | PulseAudio / PipeWire Integration |

### ZMQ Message Format
IPC messages consist of a **2-Frame ZMQ Multipart**:
- **Frame 0 (Topic)**: Routing key as bytes (e.g., `ui.widget.geometry`).
- **Frame 1 (Payload)**: JSON-encoded dictionary containing user data and `_trace` metrics.

---

## 6. Vision Timeline

- **Phase 1: Foundation** ✅
  - Multi-process layout, ZeroMQ XPUB/XSUB broker, and base `BusClient` wrapper established.
- **Phase 2: Core Features** ✅
  - UI shell layout engine, transparent PyQt6 widgets, global Input Trap routing, and video/audio projection rendering implemented.
- **Phase 3: Optimization & Hardening** 🚧 *(Active)*
  - Performance tuning for older processors, HWM drop analytics, and test coverage validation.
- **Phase 4: Deployment** 📋
  - Embedded deb packaging, target hardware profiling, and production release.

---

## 7. Success Metrics

1. **Test Coverage**: Maintain high test coverage using the standardized test suite (`pytest`).
2. **Media Performance**: Sustain $60\text{ fps}$ video projection with minimal frame drops.
3. **Transport Efficiency**: Keep message bus routing latency $\le 2\text{ ms}$ under active projection workloads.
4. **Resiliency**: Ensure that a crash in any non-critical module (e.g., config_ui) does not interrupt background audio playback or OAA socket sessions.
