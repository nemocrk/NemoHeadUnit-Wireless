# Design Spec: Phone Status, Notification & Media Browser Channels

**Date:** 2026-08-31  
**Status:** Approved  
**Scope:** Android Auto Protocol Handlers & Multi-Platform UI Widgets (Qt6 & Web GUI)

---

## 1. Overview
Implement end-to-end support for 3 new Android Auto channels:
1. **`phone_status_channel`**: Call state handling (incoming/active/ended), caller metadata, duration timer, cellular signal strength, battery level.
2. **`notification_channel`**: Heads-up popups and persistent notifications feed with action buttons.
3. **`media_browser_channel`**: Library browsing (artists, albums, playlists, tracks) inside the Media Card.

---

## 2. Architecture & Data Flow

```
                ┌────────────────────────────────────────────────────────┐
                │               Android Auto Wire Protocol               │
                └───────────────────────────┬────────────────────────────┘
                                            │
           ┌────────────────────────────────┼────────────────────────────────┐
           ▼                                ▼                                ▼
┌───────────────────────┐        ┌───────────────────────┐        ┌───────────────────────┐
│  PhoneStatusHandler   │        │  NotificationHandler  │        │  MediaBrowserHandler  │
│ (ch 10 / 0x8001/8002) │        │ (ch 12 / 0x8001/8002) │        │ (ch 11 / 0x8001-8004) │
└──────────┬────────────┘        └──────────┬────────────┘        └──────────┬────────────┘
           │                                │                                │
           ▼                                ▼                                ▼
  ZMQ: `phone.status`              ZMQ: `notification.*`           ZMQ: `media.browser.*`
  RPC: `/api/phone/action`         RPC: `/api/notif/action`        RPC: `/api/media/browse`
           │                                │                                │
           └────────────────────────────────┼────────────────────────────────┘
                                            │
                                            ▼
                       ┌────────────────────────────────────────┐
                       │          Qt6 GUI & Web GUI             │
                       │ ├─ Phone Status / Call Widget          │
                       │ ├─ Heads-up Toast & Notification Card  │
                       │ └─ Media Card "Browse" Explorer        │
                       └────────────────────────────────────────┘
```

---

## 3. Detailed Component Specifications

### 3.1 Backend Protocol Handlers (`backend/modules/channel_manager/handlers/`)

#### 1. `phone_status_handler.py`
- Advertised in `service_discovery.py` as `PhoneStatusChannel {}` (Descriptor field 10).
- Incoming Message `0x8001` (`PhoneStatusUpdate`):
  - Parse calls list (`state`, `caller_id`, `caller_name`, `call_duration`).
  - Parse phone status metrics (`signal_strength` 0–5, `battery_level` 0–100, `is_charging`, `carrier_name`).
  - Publish `phone.status` on ZMQ and broadcast over WebSocket.
- Outgoing Message `0x8002` (`PhoneStatusInput`):
  - Send call actions (`ACTION_ANSWER`, `ACTION_REJECT`, `ACTION_HANGUP`, `ACTION_MUTE`).
  - Exposed via REST endpoint `POST /api/phone/action`.

#### 2. `notification_handler.py`
- Advertised in `service_discovery.py` as `NotificationChannel {}` (Descriptor field 13).
- Incoming Message `0x8001` (`NotificationEvent`):
  - Parse `id`, `app_name`, `title`, `text`, `timestamp`, `icon_blob`, and `actions` (e.g. "Read Aloud", "Reply", "Dismiss").
  - Publish `notification.post` on ZMQ.
- Outgoing Message `0x8002` (`NotificationAction`):
  - Send trigger for user-selected action ID or dismiss.
  - Exposed via REST endpoint `POST /api/notification/action`.

#### 3. `media_browser_handler.py`
- Advertised in `service_discovery.py` as `MediaBrowserChannel {}` (Descriptor field 11).
- Wire interaction:
  - `GetRootRequest` (`0x8001`) / `GetRootResponse`: Queries top-level root nodes (Artists, Albums, Playlists).
  - `GetChildrenRequest` (`0x8002`) / `GetChildrenResponse`: Fetches child nodes for a folder/playlist.
  - `GetItemRequest` (`0x8003`) / `GetItemResponse`: Fetches specific item metadata.
  - `PlayItemRequest` (`0x8004`): Requests playback of selected media ID.
- Exposed via REST endpoints `GET /api/media/browse?node_id=...` and `POST /api/media/play_item`.

---

### 3.2 Qt6 GUI Clock/Dashboard Screen (`backend/modules/qt6_gui/`)

1. **Header Command Bar / Status Indicators**:
   - Dynamic cellular signal bars (0–5) and battery charge pill (% + charging bolt) in `CommandBar`.
2. **Phone Call Widget (`ui/phone_call_widget.py`)**:
   - Active call card on the Clock page with Contact Name, Number, Timer, and Green Accept / Red Hangup / Yellow Mute buttons.
3. **Notification System (`ui/notification_widget.py`)**:
   - Animated slide-down Heads-Up toast at the top of the window.
   - Collapsible Notification Card on the Clock page grid.
4. **Media Card Browser (`ui/media_card_widget.py`)**:
   - Flip button between "Now Playing" artwork/progress and scrollable `QListWidget` media library browser.

---

### 3.3 Web GUI Clock/Dashboard Screen (`frontend/`)

1. **Header Status Icons (`frontend/index.html` & `ui_controls.js`)**:
   - SVG cellular signal indicator and battery indicator.
2. **Web Call Widget (`js/phone_widget.js` & `css/style.css`)**:
   - In-call card on `#dashboard-grid` with action triggers to `/api/phone/action`.
3. **Web Notifications System (`js/notification_manager.js`)**:
   - Slide-down glassmorphism toast popup and dashboard notification card.
4. **Web Media Browser (`js/media_player.js`)**:
   - Integrated library browsing view in `#dashboard-media-card` querying `/api/media/browse`.

---

## 4. Verification & Testing Plan
1. Unit tests in `scratch/test_new_channels.py` verifying:
   - SDP advertisement with all new channel descriptors.
   - Handlers parsing wire protobufs (`PhoneStatusUpdate`, `NotificationEvent`, `MediaBrowserResponses`).
   - Action dispatching (`PhoneStatusInput`, `NotificationAction`).
2. Live smoke test of backend orchestrator and Qt6 GUI rendering.
