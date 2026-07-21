# Functional Targets Guide — NemoHeadUnit-Wireless V2

This document details the functional specifications and target hardware configurations for media streaming, wireless handshakes, audio routing, and Open Android Auto (OAA) protocol integration in NemoHeadUnit-Wireless V2.

---

## 1. Open Android Auto (OAA) Protocol Integration

The core function of the headunit is hosting **Android Auto wireless projection sessions** via the OAA protocol stack.

```
+-------------------------------------------------------+
| Physical Layer: Bluetooth RFCOMM (Credentials Exch.)  |
+-------------------------------------------------------+
| Link Layer: Wi-Fi 5GHz AP (hostapd_helper / dnsmasq)  |
+-------------------------------------------------------+
| Transport Layer: TCP Socket Connection (Port 5277)    |
+-------------------------------------------------------+
| Session Layer: SSL / TLS Secure Handshake             |
+-------------------------------------------------------+
| Application Layer: OAA Multiplexed Control & Streams  |
+-------------------------------------------------------+
```

### Handshake & Session Establishment
1. **Bluetooth Discovery**: The phone pairs with the headunit via Bluetooth.
2. **RFCOMM Handshake**:
   - The headunit listens on an RFCOMM socket.
   - Upon connection, it sends Wi-Fi details to the phone: SSID, WPA2 security passphrase, IP address, and target TCP port (default `5277`).
3. **Wi-Fi Association**: The phone disconnects BT or runs it in background, switches Wi-Fi on, associates with the headunit AP, and obtains an IP via DHCP.
4. **TCP Control Link**: The phone opens a socket to the headunit TCP server on port `5277`.
5. **OAA Init**:
   - **Version Handshake**: `VERSION_REQUEST` (0x0001) / `VERSION_RESPONSE` (0x0002).
   - **SSL Handshake**: TLS session establishment over Channel 0.
   - **Auth Complete**: `AUTH_COMPLETE` (0x0004).
   - **Service Discovery**: The phone queries available capabilities. The headunit advertises video projection sizes, audio formats (media and voice guidance), and input capabilities (touchscreen, physical buttons).

---

## 2. Bluetooth RFCOMM & Wi-Fi Access Point Orchestration

### Wi-Fi AP Management (`hostapd_helper`)
To provide the high bandwidth and low latency required for video projection, the headunit orchestrates a **5GHz Wi-Fi Access Point**:
- **Interface Control**: Spawns and monitors `hostapd` and `dnsmasq` processes.
- **5GHz Selection**: Configures DFS (Dynamic Frequency Selection) channels to avoid interference.
- **State Reporting**: Publishes client connection state changes (`wifi.client.connected`, `wifi.client.disconnected`) on the ZMQ bus.

### Bluetooth RFCOMM Client/Server Handshakes (`rfcomm_handshake`)
- **Protocol**: RFCOMM profile socket listening.
- **SDP Registration**: Registers Service Discovery Protocol (SDP) record with Android Auto UUID `00001101-0000-1000-8000-00805F9B34FB`.
- **Payload Exchange**: Package exchange containing JSON-encoded Wi-Fi credentials for secure authentication.

---

## 3. Media Pipelines & GStreamer Integration

Projection video is received as fragmented H.264 stream frames over ZMQ and rendered onto the screen.

```
       +----------------------------+
       |   ZMQ H.264 Video Frames   |
       +----------------------------+
                      |
                      v
       +----------------------------+
       |   appsrc (GStreamer input) |
       +----------------------------+
                      |
                      v
       +----------------------------+
       | h264parse (NAL parsing)    |
       +----------------------------+
                      |
                      v
       +----------------------------+
       |   v4l2h264dec / nvdec /    |
       |  decodebin (HW Decoding)   |
       +----------------------------+
                      |
                      v
       +----------------------------+
       |   glimagesink /            |
       | autovideosink (Renderer)   |
       +----------------------------+
```

### Video Pipelines
The `video_ui` module integrates with GStreamer via PyGObject (`gi.repository.Gst`).

- **Base GStreamer Pipeline**:
  ```
  appsrc name=video_src format=time is-live=true ! h264parse ! decodebin ! videoconvert ! autovideosink
  ```
- **Hardware Acceleration Support**:
  - **Raspberry Pi 4 / 5**: Uses `v4l2h264dec` with hardware overlay support.
  - **x86_64 / Nvidia**: Configures VAAPI (`vaapih264dec`) or NVDEC (`nvh264dec`) depending on hardware drivers.
  - **Fallback**: Software decoding (`openh264dec` or `avdec_h264`) when hardware acceleration is unavailable.

---

## 4. Audio Routing & Management

Android Auto streams audio in separate virtual channels depending on the stream type to handle mixing correctly:
1. **Media Channel (AAC, 48kHz Stereo)**: High-quality music/audio stream.
2. **Guidance Channel (PCM, 16kHz Mono)**: Navigation prompts.
3. **Voice Input/System Channel (PCM, 16kHz Mono)**: Microphone voice control.

### PulseAudio & PipeWire Integration
The `audio_manager` maps these virtual streams to OS-level audio devices:
- **Device Enumeration**: Scans available sound cards using `pactl` (PulseAudio) or `pw-cli` (PipeWire).
- **Sound Mapping**:
  - Media audio is routed to the vehicle's primary speakers sink.
  - Navigation audio is mixed dynamically (lowering media volume momentarily).
- **Microphone Loopback**: Routes microphone inputs back to the OAA session TCP socket during voice commands.
