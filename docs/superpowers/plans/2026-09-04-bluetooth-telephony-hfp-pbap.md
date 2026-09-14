# Standalone Bluetooth Telephony Implementation Plan (HFP Audio & Controls + PBAP Phonebook)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore bidirectional Bluetooth call audio on Linux/PipeWire, build a 100% functional standalone Bluetooth telephony stack (HFP AT commands + PBAP contacts & recents sync) driving the Qt6 GUI Phone Drawer, and replace the dashboard notification card with a dedicated Phone Telephony Card Widget with quick actions and open drawer button.

**Architecture:** 
1. Fix PipeWire BlueZ SPA configuration on target (`bluez5.hfphsp-backend = "native"` in `50-bluez.conf`) and implement dynamic loopback routing in `connectivity_manager` for bidirectional call audio (`bluez_input` -> car speakers, mic -> `bluez_output`).
2. Add an HFP Telephony Client to `connectivity_manager` for standalone dialing (`ATD`), answering (`ATA`), hanging up (`AT+CHUP`), muting (`AT+CMUT`), DTMF (`AT+VTS`), and live call status polling (`+CLCC`).
3. Add a PBAP vCard Client via BlueZ OBEX (`org.bluez.obex.PhonebookAccess1`) to fetch and cache Contacts (`telecom/pb`), Favorites (`telecom/fav`), and Call History (`telecom/cch`).
4. Hydrate Qt6 GUI `phone_drawer.py` with live contacts search, real call history, favorites, and dialer actions.
5. Create `PhoneCardWidget` replacing the notification card on the clock dashboard with cellular signal, battery, carrier, quick actions, and a button to open the Phone Drawer.

**Tech Stack:** Python 3.13/3.14, D-Bus (`dbus-python`), BlueZ 5 (`org.bluez`, `org.bluez.obex`), PipeWire / WirePlumber (`pactl` / `pw-link`), PyQt6, `pytest`.

---

## Tasks

### Task 1: PipeWire BlueZ Configuration Fix & Audio Loopback
- [x] **Step 1.1**: Update `packaging/50-bluez.conf` with `bluez5.hfphsp-backend = "native"` and modern PipeWire 1.0+ settings.
- [x] **Step 1.2**: Update `/etc/wireplumber/wireplumber.conf.d/50-bluez.conf` on target device (`192.168.1.105`) and restart wireplumber.
- [x] **Step 1.3**: Implement `ensure_hfp_loopback` in `backend/shared/hardware/linux_audio.py` to automatically bridge `bluez_input.*` to `@DEFAULT_SINK@` and `@DEFAULT_SOURCE@` to `bluez_output.*`.
- [x] **Step 1.4**: Add automated test in `tests/test_hfp_audio_loopback.py` verifying loopback activation and teardown.

### Task 2: Standalone HFP Telephony Client (`backend/shared/hardware/bluez_hfp.py`)
- [x] **Step 2.1**: Write failing unit test `tests/test_bluez_hfp.py` testing AT command generation (`ATD`, `ATA`, `AT+CHUP`, `AT+VTS`, `AT+CMUT`) and call status state machine.
- [x] **Step 2.2**: Implement `BlueZHFClient` in `backend/shared/hardware/bluez_hfp.py` with mock driver for non-Linux/test environments.
- [x] **Step 2.3**: Expose REST endpoints in `backend/modules/connectivity_manager/main.py` (`/api/connectivity/phone/dial`, `/action`, `/dtmf`).
- [x] **Step 2.4**: Run tests and commit.

### Task 3: Bluetooth PBAP Contacts & Recents Sync (`backend/shared/hardware/bluez_pbap.py`)
- [x] **Step 3.1**: Write failing unit test `tests/test_pbap_vcard_parser.py` verifying vCard parsing for contacts, telephone types, and timestamps.
- [x] **Step 3.2**: Implement `BlueZPBAPClient` with stdlib vCard parser and local JSON caching in AppData.
- [x] **Step 3.3**: Expose REST endpoints in `backend/modules/connectivity_manager/main.py` (`/api/connectivity/phone/contacts`, `/recents`, `/favorites`, `/sync`).
- [x] **Step 3.4**: Run tests and commit.

### Task 4: Phone Drawer Real Data Hydration & Keypad Integration
- [x] **Step 4.1**: Write failing GUI test in `tests/test_phone_drawer_hydration.py`.
- [x] **Step 4.2**: Update `backend/modules/qt6_gui/ui/drawers/phone_drawer.py` to populate real PBAP data, search filter in Contacts tab, and call click handling.
- [x] **Step 4.3**: Wire dialer keypad to send `ATD` when idle and DTMF `AT+VTS` when in-call.
- [x] **Step 4.4**: Verify full test suite passes (54+ tests) and commit.

### Task 5: Dashboard Phone Card Widget (Replacing Notification Card)
- [x] **Step 5.1**: Create `backend/modules/qt6_gui/ui/phone_card_widget.py` with phone status (carrier, signal bars, battery level, device name), quick call action, and "Open Phone Drawer" button.
- [x] **Step 5.2**: Update `backend/modules/qt6_gui/ui/main_window.py` to replace `notification_card` with `phone_card_widget`, connect "Open Phone Drawer" button to toggle `self.phone_drawer`.
- [x] **Step 5.3**: Add automated test `tests/test_phone_card_widget.py` and verify in full test suite.
- [x] **Step 5.4**: Commit and perform final smoke test.
