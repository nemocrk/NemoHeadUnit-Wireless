# Project Roadmap — NemoHeadUnit-Wireless

This document tracks the milestone roadmap and active development status of NemoHeadUnit-Wireless.

---

## 1. Milestone Status Overview

| Milestone | Description | Status | Target |
|---|---|---|---|
| **Phase 1: Foundation** | Transition from monolithic event loop to isolated ZeroMQ processes. | **Completed** | Q1 2026 |
| **Phase 2: Core V2 Promotion** | Promote V2 codebase to repository root, physically delete legacy `app/` files, and implement core UI shell composition. | **Completed** | Q2 2026 |
| **Phase 3: Hardening & Optimization** | Performance tuning for edge devices, test coverage expansion, and network resiliency adjustments. | **Active** | Q3 2026 |
| **Phase 4: Packaging & Production** | Final packaging (`.deb`), automated deployment tooling, and vehicle target verification. | **Planned** | Q4 2026 |

---

## 2. Completed Phase 2 Sub-Tasks

All components of the V2 promotion and base UI implementation have been successfully integrated:

1. **Repository Promotion**:
   - V2 codebase promoted to root (`main.py`, `bus_broker.py`, `shared/`, `modules/`).
   - Obsolete V1 `app/` folder and fragmented v1 documents deleted.
2. **UI Core Layers**:
   - `modules/ui_shell/`: Implemented layout reflow engine and transparent `InputTrap` overlay.
   - `modules/navbar_ui/`: Implemented bottom playback bar with tap targets.
   - `modules/floating_menu_ui/`: Implemented arc-based launcher for requested modules.
3. **Core Documentation**:
   - Foundations suite drafted (`SYSTEM_ARCHITECTURE.md`, `DESIGN_PATTERNS.md`, `FUNCTIONAL_TARGETS.md`).

---

## 3. Active Phase 3 Tasks: Hardening & Optimization

### High-Priority Optimization Targets
- **ZMQ High Water Mark Tuning**: Validate socket behaviors under bursty log and raw video frames traffic to prevent `zmq.Again` drops.
- **Hardware-Accelerated Video UI**: Benchmark GStreamer pipelines (`v4l2h264dec`, `vaapih264dec`) on target Atom/ARM edge devices.
- **Headless Test Suite Alignment**: Ensure full test suite executions can run headlessly on CI runners without real display cards (virtual framebuffers).

---

## 4. Priority Conventions

| Priority | Module / Service | Purpose |
|---|---|---|
| **0** | `config_manager` | Bootstraps configuration parameters. |
| **1** | `bluetooth_manager`, `audio_manager`, `tcp_server` | Core network/hardware services. |
| **2** | `ui_shell` | Initiates layout engine and global event listener. |
| **3** | `floating_menu_ui` | Listens for priority 4 registration requests. |
| **4** | `navbar_ui`, `video_ui`, `bluetooth_ui`, `config_ui` | Widget processes that display UI panels. |

---

*Roadmap Version: 6.0*  
*Last Updated: 2026-06-11*  
*Author: Nemo Development Team*  
