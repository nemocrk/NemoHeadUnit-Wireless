# Project Vision: NemoHeadUnit-Wireless

## Executive Summary

NemoHeadUnit-Wireless is a full-stack application designed to emulate an Android Auto head unit experience on edge devices. The system enables remote control and configuration of Android Auto functionality through wireless connections, providing a powerful tool for drivers and developers alike.

## Vision Statement

**To create a high-performance, Python-based emulation platform that brings the full Android Auto experience to edge devices, enabling seamless wireless connectivity, real-time media streaming, and comprehensive head unit configuration—all without requiring C++ development.**

**To build a modular, not-giant-blobs architecture that avoids monolithic code structures, ensuring maintainability, scalability, and separation of concerns.**

---

---

## 1. Primary Purpose

The primary purpose of NemoHeadUnit-Wireless is to emulate an Android Auto head unit, allowing users to:
- Control and configure Android Auto functionality remotely
- Stream video and audio channels from the smartphone
- Route microphone audio to the head unit when requested
- Provide a touch-friendly user interface for interaction

---

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
- **Video Channel**: Projection of Android Auto video streams
- **Audio Channel**: Reproduction of Android Auto audio streams
- **Microphone Routing**: Dynamic mic routing to head unit when requested by Android Auto

### 3.3 Configuration System
- **Head Unit Configuration**: Full configuration of every aspect of Android Auto
- **Associated Stack**: Complete integration with Android Auto stack elements
- **Touch UI**: Intuitive, touch-mode user interface for interaction

### 3.4 Technical Implementation
- **Python-Only Architecture**: Modern architecture leveraging Python 3.14
- **Shared Message Bus**: Absolute decoupling where every communication between modules—including lifecycle events such as instantiation and starting—is performed through the internal message bus. See Bus-Driven Lifecycle.
- **Multi-threaded Design**: All components connected to the internal shared message bus for high parallelism.

---

## 4. Technical Architecture

### 4.1 Architecture Principles
- **Python-Only**: No C++ dependencies; pure Python implementation
- **High Parallelism**: Multi-threaded design using an internal shared message bus connecting all components
- **Message-Bus Centricity**: Every interaction between modules, from instantiation and initialization to runtime data exchange, is strictly routed through the internal shared message bus to ensure complete isolation and modularity. This follows the Bus-Driven Lifecycle pattern.
- **Logging**: Per-module verbosity control implemented via the Logger module (app/logger.py)

---

### 4.2 Performance Requirements
- **Low CPU Optimization**: High performance on resource-constrained devices
- **Parallel Processing**: Efficient thread utilization through message bus
- **Edge Deployment**: Optimized for embedded systems

### 4.3 Deployment Stack
- **Environment**: Python 3.14 via conda environment (`py314`)
- **Build System**: Local build and deployment through SSH
- **Package Format**: Bundled deb package
- **Target OS**: Ubuntu 24 (Edge device deployment)

---

## 5. Non-Functional Requirements

### 5.1 Performance
- **High Performance**: Optimized for low CPU peripherals
- **Parallelism**: Through internal shared message bus architecture
- **Latency**: Minimal latency in media streaming and control

### 5.2 Reliability
- **Stable Connection**: Reliable wireless protocols (Bluetooth/WiFi AP)
- **Failover**: Automatic fallback between connection methods
- **Recovery**: Graceful degradation when features fail

### 5.3 Security
- **Secure Pairing**: Encrypted Bluetooth pairing
- **Secure Transmission**: Encrypted WiFi AP communication
- **Authentication**: Secure connection authentication

---

## 6. Deployment Environment

### 6.1 Target Platform
- **Edge Devices**: Embedded systems for automotive use
- **Operating System**: Ubuntu 24
- **Processor**: Optimized for older Atom processors

### 6.2 Deployment Method
- **Local Build**: Application built locally
- **SSH Deployment**: Automated deployment via SSH
- **Package Format**: Bundled deb package for easy installation

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
- **Processor Limitation**: Optimized for older Atom processors
- **Resource Optimization**: High performance on low CPU peripherals
- **Python Limitations**: No C++ dependencies; pure Python implementation

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
- **Atom Processor**: Optimized for older Atom processor capabilities

### 10.2 User Behavior Assumptions
- **Driver Safety**: Users prioritize hands-free operation
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
| Environment | Conda (py314) |
| OS | Ubuntu 24 |
| Processor | Atom (older generation) |
| Deployment | deb package via SSH |
| Connection | Bluetooth + WiFi AP |
| Build | Local build, SSH deploy |
| Logging | Per-module verbosity control (app/logger.py) |

---

## 12. Vision Timeline

**Phase 1: Foundation**
- Core Python architecture implementation
- Message bus architecture
- Basic connection protocols

**Phase 2: Core Features**
- Video/audio streaming
- Remote configuration
- Touch UI development

**Phase 3: Optimization**
- Performance optimization for Atom processors
- Test coverage expansion
- Reliability improvements

**Phase 4: Deployment**
- Edge device deployment
- Production deployment via SSH
- Comprehensive testing

---

## 13. Key Differentiators

1. **Pure Python**: No C++ dependencies; accessible to Python developers
2. **Bus-Driven Architecture**: Total modular isolation; even module instantiation and startup are triggered via the shared message bus. Refer to the detailed architectural documentation.
3. **Edge Optimization**: Optimized for older Atom processors
4. **Wireless Focus**: Bluetooth + WiFi AP for redundancy
5. **Modular Logging**: Per-module verbosity control for debugging and troubleshooting

---

## 14. Future Considerations

- **Nemo Integration**: Potential alignment with Nemo ecosystem
- **Android Auto Updates**: Compatibility with future Android Auto versions
- **Security Enhancements**: Enhanced security protocols
- **Performance Improvements**: Continuous optimization for edge devices
- **Enhanced Logging**: Expanded logging capabilities with per-module control

---

## Conclusion

NemoHeadUnit-Wireless represents a significant advancement in Android Auto emulation technology, providing drivers with a powerful, wireless, touch-friendly interface for controlling their Android Auto experience. By leveraging a multi-threaded and message-bus-driven Python 3.14 architecture and optimizing for edge devices, the project achieves high performance and modularity without the complexity of C++ development.

**Success is measured by comprehensive test coverage and exceptional ease of use.**

---

*Document Version: 2.0*
*Last Updated: [Date]*
*Author: Nemo Development Team*