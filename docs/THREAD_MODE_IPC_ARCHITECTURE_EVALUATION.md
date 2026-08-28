# Thread-Mode Intra-Module Communication Evaluation

## Scope and conclusion

This evaluation covers the active `backend/` implementation (the eight module directories excluding `_template`) and not `legacy_2026_07` or vendored `third_party/`. It was performed from source inspection; no throughput or latency benchmark was available in the repository.

The current `multithreading` mode only changes how module entry points are launched. It does **not** change how modules communicate. Each module still has an independent ZeroMQ context, PUB/SUB socket pair, subscriber thread, asyncio loop, and (where applicable) a loopback HTTP server. Messages still traverse the XSUB/XPUB broker over Unix sockets (or loopback TCP on Windows), are JSON encoded and decoded, and are dispatched across at least two threads.

Therefore thread mode currently trades away process isolation without receiving the intended in-memory communication benefit. The recommended target is a dual transport design: preserve ZMQ IPC for process mode and introduce a bounded, typed in-process event router plus direct media ownership transfer for thread mode.

## Current topology

```text
                     lifecycle, config, control, input
module event loop  -> JSON/PUB -> Unix socket -> XSUB/XPUB -> Unix socket
                                                         -> SUB thread -> module event loop

phone TCP -> tcp_server -> downstream SHM -> channel_manager -> transcode SHM
                                                        -> media_server -> output SHM -> Qt GUI
```

The active topology has these components:

| Communication purpose | Current transport | Notes |
| --- | --- | --- |
| Lifecycle, configuration, control, input | ZMQ PUB/SUB, JSON, `ipc:///tmp` on POSIX | Broker is a single global fan-out point. |
| Phone projection link | TCP socket, TLS, framed AA protocol | Must remain a socket boundary. |
| Media payloads | `multiprocessing.shared_memory` plus ZMQ notification | Payload is not sent via ZMQ, but it is copied to and from SHM. |
| Module HTTP API | Individual `aiohttp` loopback servers | Needed as a public/process boundary, but unnecessary between colocated modules. |
| GUI status | GUI -> public proxy -> loopback module SSE, read in worker threads | Uses the proxy even though caller and target are in the same process in thread mode. |
| Browser access | HTTP/WebSocket through proxy | Correct public boundary; retain it. |
| Bluetooth/Wi-Fi OS integration | D-Bus/GLib and RFCOMM callbacks | Correct external boundary; keep its dedicated integration threads. |

### Thread count and ownership

Thread mode starts a module thread per module (`backend/main.py:87-101`). Each `BusClient` then starts another subscriber thread (`backend/shared/bus_client.py:60-87`), and the broker starts a proxy thread. TCP reception, Bluetooth/GLib, GStreamer, Qt worker requests, and some SSE readers add more threads. This is materially more scheduling and context-switch overhead than a single-process event-router design, and it makes a shared-Python-process shutdown less deterministic.

## Findings

### P0: Thread mode retains the full IPC stack

`BusClient` constructs `zmq.Context()` for every module and connects its sockets to the external bus address (`backend/shared/bus_client.py:20-41`). The broker itself is launched as another module thread (`backend/main.py:297-310`). Since `inproc://` requires the same ZMQ context and none is shared, thread mode cannot use ZMQ's in-process transport as written. On POSIX the message still passes through Unix-domain sockets and the broker; on Windows it goes through loopback TCP.

For every non-media event this means topic and payload encoding, kernel/ZeroMQ queueing, broker forwarding, decoding, callback-thread execution, and often coroutine scheduling. This dominates the useful work for small control and input messages.

### P0: The shared-memory media path is not zero-copy at the Python level

The ring writes `payload` into the shared-memory buffer (`backend/shared/media_shm.py:80-109`) and `read_frame` creates a new `bytes` object from the buffer (`backend/shared/media_shm.py:111-131`). It avoids a ZMQ payload copy across *processes*, but it still copies each payload into SHM and back out on every reader.

The H.264 route has two SHM hops:

1. `tcp_server` writes an AA media body to `downstream` and publishes `aa.frame.shm` (`backend/modules/tcp_server/main.py:281-310`).
2. `channel_manager` reads that body, parses it, writes the codec payload to `transcode_in`, and publishes `media.video.raw_nal_shm` (`backend/modules/channel_manager/main.py:289-307`, `handlers/video_handler.py:130-159`).
3. `media_server` reads it into a new `bytes` object before feeding the decoder (`backend/modules/media_server/main.py:467-480`).

Decoded video takes a further output SHM write/read before Qt uploads it to OpenGL (`backend/modules/media_server/main.py:555-575`, `backend/modules/qt6_gui/main.py:371-376`). Thread mode can pass an immutable `bytes`/`memoryview` frame descriptor directly from TCP to the media pipeline and eliminate the intermediary SHM hop entirely.

### P0: The SHM ring has no concurrency or lifetime protocol

`RingSharedMemoryBuffer` has one local write offset and no shared producer index, reader index, sequence number, reservation state, lock, atomic publish flag, reference count, or backpressure (`backend/shared/media_shm.py:78-131`). A producer can wrap and overwrite an unread slot; readers can observe a header before the payload is fully written; a notification can outlive the data it refers to. The code normally tolerates this by returning an empty/invalid payload, which converts overload into silent media loss.

This is unsafe in both modes. In thread mode it is especially unnecessary because a bounded in-memory queue can provide explicit ownership and a deliberate drop policy. In process mode, retain SHM only after giving it a proper single-producer/multi-consumer publication protocol.

### P1: Media is duplicated onto JSON topics even after SHM notification

For every media frame, `tcp_server` always also builds `payload_hex` dictionaries and publishes both `aa.frame.received` and `aa.frame.ch<N>` (`backend/modules/tcp_server/main.py:295-310`). `channel_manager` ignores these media messages only after JSON decoding and hex-to-bytes reconstruction (`backend/modules/channel_manager/main.py:317-331`). This creates two hex strings and extra bus traffic for payloads already delivered by SHM.

Audio has another expensive conversion: `channel_manager` base64 encodes the binary frame for `media.audio.frame` (`handlers/audio_handler.py:131-137`), then `media_server` base64 decodes it and writes it to SHM for Qt (`backend/modules/media_server/main.py:442-462`). It also streams the same audio to its own WebSocket clients while `channel_manager` has a separate WebSocket broadcaster. This duplicates CPU work and creates ambiguous ownership of the browser media route.

### P1: ZMQ socket ownership is not safe during subscription changes

The bus starts its receive thread before modules finish registration. `BaseBackendModule.start()` calls `bus.start()` before it calls module `setup()`; setup calls `subscribe()`. Consequently `_sub.poll()`/`recv_multipart()` in the listener thread can race with `_sub.setsockopt_string(ZMQ_SUBSCRIBE)` in the event-loop thread (`backend/shared/base_module.py:329-359`, `backend/shared/bus_client.py:45-87`). ZMQ sockets are not thread-safe. The broad exception handler suppresses the evidence and retries, so missed subscriptions and socket errors may be hard to diagnose.

### P1: Colocated UI calls route through the public proxy and blocking workers

The Qt module opens SSE connections to `http://127.0.0.1:8000`, which enter the proxy and then another module's loopback server. It reads those streams through `urllib` inside `asyncio.to_thread` (`backend/modules/qt6_gui/main.py:253-295`). Drawer and volume UI code use the same loopback proxy route. This adds HTTP parsing, proxy copying, persistent connection state, and worker threads to local state notifications.

The proxy remains necessary for browser clients and external HTTP access. It should not be the in-process service locator in thread mode. Likewise, `BaseBackendModule.call_module()` should resolve to a registered in-memory handler in thread mode instead of HTTP.

### P1: Thread-mode fault containment and shutdown are weaker than process mode

In thread mode all modules share interpreter state, logging sinks, imported-module globals, and the GIL. A CPU-heavy Python callback can delay every other module. A native-library crash still terminates the process. The orchestrator can only join a module thread for up to two seconds; it cannot terminate it (`backend/main.py:145-156`). `system.stop` must first traverse the same broker that thread mode ought to bypass.

This makes thread mode appropriate for reduced overhead during development or for a carefully bounded embedded deployment, not as a transparent replacement for process supervision.

## Recommended target architecture

Use an execution-mode-aware transport facade, keeping module-level APIs stable:

```text
                     Process mode                         Thread mode
events        BusClient -> ZMQ XPUB/XSUB           EventRouter -> asyncio queues
service calls aiohttp loopback HTTP                direct registered async handler
media         SHM descriptor + control event       FrameRef / memoryview + bounded queue
browser API   proxy -> HTTP/WebSocket              proxy -> HTTP/WebSocket (unchanged)
OS/phone I/O  TCP, D-Bus, RFCOMM (unchanged)       TCP, D-Bus, RFCOMM (unchanged)
```

### Event router requirements

Introduce an `InProcessBus` behind the existing `publish`/`subscribe` interface. It should:

- Register all subscriptions before startup, then deliver callbacks onto the owning module's asyncio loop via `loop.call_soon_threadsafe`.
- Use bounded queues per subscriber and an explicit event class: reliable control/lifecycle events block or report failure; telemetry and high-rate input can coalesce or drop by policy.
- Support exact topics and a documented prefix/wildcard contract. The present `startswith(sub_topic.rstrip("*"))` behavior treats every registered topic as a prefix.
- Carry typed Python objects internally. JSON conversion belongs exclusively at external interfaces and persisted/network boundaries.
- Expose metrics: queue depth, dispatch latency, coalesced/dropped counts, handler duration, and per-topic rate.

Do not make synchronous direct calls from publishers to subscribers. That would reduce copies but permits re-entrant state changes and lets a slow consumer block TCP reception. Queueing to the target loop preserves module ownership and scheduling isolation.

### Media ownership model

For thread mode define an immutable `FrameRef` containing channel ID, message ID, timestamp, payload (`memoryview` or immutable `bytes`), and release/drop policy. Send this through bounded single-consumer queues:

```text
TCP receive -> channel manager media dispatcher -> media server decoder -> Qt renderer
```

Pass the parsed payload once and avoid `hex`, base64, SHM copies, and duplicate WebSocket broadcasts. Keep a small latest-frame queue for video (drop stale frames intentionally) and a bounded time-based audio queue with a clear overflow policy. The thread-mode renderer still has to upload pixels to GPU; that copy is intrinsic to the current OpenGL API, but the CPU-side intermediate copies are not.

For process mode, either retain SHM with a sequence-checked slot protocol and acknowledgements/reference counts, or use a mature local transport. Do not share the thread-mode `FrameRef` across a process boundary.

## Migration plan

1. **Measure before changing behavior.** Add a transport-neutral trace ID and monotonic timestamps at TCP receive, channel dispatch, decoder feed, decoder output, Qt paint submission, and browser write. Record p50/p95/p99 latency, frame drops, queue depths, CPU, and RSS in both modes.
2. **Make topic semantics safe.** Register subscriptions before receiver start, narrow the exception handling in `BusClient`, and classify topics as reliable, lossy/latest, or telemetry. Add tests for subscription races and ordering.
3. **Add `InProcessBus` and a module registry.** Select it only when `--mode multithreading`; keep `BusClient` in process mode. Migrate lifecycle/config/control/input first, where typed events give immediate benefit with low media risk.
4. **Replace internal HTTP/SSE calls.** Route Qt status and `call_module()` through the registry in thread mode. Retain HTTP endpoints and proxy behavior for browsers and process mode.
5. **Collapse media ownership.** Introduce `FrameRef` queues and change the TCP -> channel-manager -> media-server path. Remove `aa.frame.received`/`aa.frame.ch<N>` payload publication for known media messages; publish only compact diagnostics when enabled.
6. **Harden process-mode SHM independently.** Add publication sequencing, generation checks on wrap, explicit capacity/backpressure, and tests for concurrent producer/consumer and slow-consumer overwrite. This is a correctness change, not a thread-mode optimization.
7. **Re-evaluate the mode boundary.** Keep a hybrid option: TCP, decoder, and Qt colocated for latency while configuration/proxy/connectivity remain separately supervised if reliability warrants it.

## Verification criteria

The implementation should be accepted only when thread mode demonstrates, under a sustained projection session:

- no ZMQ broker, Unix-domain IPC, JSON, hex, or base64 on the media hot path;
- bounded queues with visible, intentional overload behavior rather than silent SHM corruption/loss;
- ordered delivery for lifecycle/control and latest-frame behavior for video;
- no ZMQ socket use from more than one thread;
- UI status updates that do not traverse public loopback HTTP in thread mode;
- clean shutdown with all worker tasks joined/cancelled and no leaked shared-memory segments; and
- regression coverage for both execution modes, including shutdown, subscription timing, overload, and end-to-end media ordering.

## Test coverage gap

The current tests cover a basic SHM write/read and media-frame packing, but do not cover bus ordering, subscription timing, backpressure, ring wrap while consumers lag, thread-mode lifecycle, or end-to-end transport selection. The optimization should land with targeted tests and a repeatable benchmark harness; otherwise improvements will be hard to distinguish from a different drop pattern.

## Qt6 GUI and standalone-mode evaluation

### The current Qt module is not standalone

The existing Qt entry point is a regular `BaseBackendModule`, so it starts a `BusClient`, waits for `system.start`, fetches configuration through the bus, and only then calls `setup` (`backend/shared/base_module.py:329-389`). The direct kiosk launcher executes this module alone (`scripts/launch_qt_kiosk.sh`), but does not start `bus_broker` or publish `system.start`. As a result, a true direct launch waits indefinitely before constructing the `QApplication` or showing a window.

Even if that lifecycle wait were bypassed, the GUI assumes the rest of the platform exists:

| Qt feature | Current dependency | Failure mode when alone |
| --- | --- | --- |
| Video and playback audio | SHM buffers plus media-frame notifications | `connect_shm()` can create missing named buffers while attaching, leaving an apparently connected but inert media path. |
| Microphone uplink | SHM plus `media.audio.mic_shm` subscriber | Captured data has no consumer. |
| Focus and touch | ZMQ events `media.video.request_focus` and `input.event` | Commands disappear without a consumer. |
| Connectivity status | Proxy-routed SSE from connectivity manager | Reconnect loops and background worker threads continue when endpoint is absent. |
| Bluetooth drawer and scan | Proxy-routed SSE and HTTP POST | UI is coupled to a fixed `127.0.0.1:8000` service endpoint. |
| Settings | Proxy-routed config HTTP API | The settings drawer cannot read or save its own configuration locally. |
| Volume | Proxy-routed media-server SSE and HTTP POST | Local optimistic state diverges from unavailable service state. |
| Logs | Intended external log stream but no client wiring | The drawer is present but no stream is attached. |

Standalone should mean **the Qt application is independently bootable and useful with zero services present**. It cannot mean Android Auto projection works without a phone/link/channel/media provider; those capabilities should instead appear as unavailable and be enabled only by adapters supplied at composition time.

### P0: Qt must own the main thread

In orchestrator thread mode, `_start_module` launches `qt6_gui` in a Python worker thread (`backend/main.py:87-101`). The module then creates `QApplication` and all `QWidget`/OpenGL/audio objects there (`backend/modules/qt6_gui/main.py:178-249`). Qt requires the GUI application and widget affinity to be in the operating system process's main thread on supported desktop platforms. `app.processEvents()` called manually every 16 ms (`main.py:253-265`) is not a substitute for having the normal Qt event loop owned by that thread.

This is the first architectural change needed for a thread-mode Qt deployment:

- the application host creates `QApplication` in the process main thread;
- `qasync` (already declared in both environment files) provides the main-thread Qt/asyncio event loop;
- non-UI services run as async tasks or worker threads and dispatch UI updates using queued Qt signals; and
- no module or callback other than the Qt main thread touches widgets, `QOpenGLWidget`, `QAudioSink`, `QAudioSource`, or `QTimer`.

### P0: widgets contain transport policy and fixed service names

The UI widget layer directly imports `urllib`, starts `QThread`s, embeds proxy URLs, and owns reconnection behavior:

- `BluetoothDrawerWidget` performs its own SSE connection and scan request (`ui/drawers/bluetooth_drawer.py:21-65`).
- `SettingsDrawerWidget` fetches and writes configuration through `/api/config` (`ui/drawers/settings_drawer.py:34-75`).
- `VolumePopoverWidget` starts an SSE reader and makes volume HTTP requests itself (`ui/volume_popover.py:15-64`).
- `Qt6GuiModule` separately runs two proxy-routed SSE readers (`qt6_gui/main.py:253-295`).

This is hard coupling in the practical sense: a reusable widget cannot be used with a local service, an in-process service, a test fake, or a remote connection without altering the widget. It also produces duplicate subscriptions and unbounded thread creation as drawers open and close.

The widgets should be passive views: Qt signals express user intent and view methods accept display models. Controllers/presenters subscribe to application ports and apply models through queued signals. URLs, ZMQ topics, retries, data mapping, and availability are adapter concerns, not widget concerns.

### P1: Qt media currently adds avoidable copies and ambiguous format handling

`QtSHMMediaEngine` reads payloads as copied `bytes`, slices RGBA again, and optionally decodes compressed images to a new RGBA buffer (`media/shm_media_engine.py:48-97`). `VideoViewportWidget` retains that `bytes` object and uploads it with `glTexImage2D` (`ui/video_viewport.py:47-113`). The GPU upload is expected in the current renderer, but the SHM copy, `payload[12:]` slice, and intermediate format conversion are avoidable in embedded thread mode.

The GUI should receive an explicit `VideoFrame` contract with format, dimensions, stride, timestamp, and ownership. It must not infer format from magic bytes. The presenter should retain only the most recent renderable frame and request a queued repaint; the renderer should use `glTexSubImage2D` when resolution is unchanged to avoid reallocating the texture every frame.

`QtAudioEngine` also initializes its output buffer to 250 ms while its software queue is capped at 50 ms (`media/audio_handler.py:78-153`). The stated 50 ms latency target cannot be met while the output device itself buffers 250 ms. AAC decoding and resampling happen on the Qt/UI thread in `play_pcm_frame`; a decoding stall can delay input and paint processing. The new audio adapter should run decode work outside the GUI thread, use timestamped bounded audio blocks, and make device-buffer latency a configuration validated against hardware support.

### P1: action wiring is incomplete

Several UI signals are declared but are not connected by `MainWindow._connect_signals`: `VolumePopoverWidget.vol_action`, `BluetoothDrawerWidget.scan_requested`, `SettingsDrawerWidget.save_config_requested`, `LogsDrawerWidget.filter_changed`, `CommandBarWidget.playpause_clicked`, and `ArcRadialMenuWidget.wifi_clicked`. The corresponding widgets instead call HTTP themselves where implemented; logs has no live client at all. This is a useful seam for the redesign: route all actions through the controller layer and remove the per-widget network workers.

## Standalone and embedded Qt design

Support three explicit compositions rather than one Qt module with hidden assumptions:

| Composition | What runs | Expected capabilities |
| --- | --- | --- |
| `qt-standalone` | Qt shell and local preferences only | Clock, layout, local theme/fullscreen settings, local audio-device diagnostics. Projection/BT/settings for other modules are visibly unavailable. |
| `qt-embedded` | Qt shell in the main thread plus selected same-process providers | Lowest-latency Android Auto display; providers communicate through typed in-process ports and bounded queues. |
| `full` | Existing supervised multi-process deployment | Browser proxy and ZMQ/SHM adapters remain supported. |

The first two must not import module implementation classes into `qt6_gui`. Instead, create a small contracts package, for example `backend/contracts/`, containing only stable dataclasses, enums, protocols, and event types. Keep it free of Qt, ZMQ, aiohttp, hardware, and module imports.

Recommended narrow ports:

| Port | GUI requests | GUI receives |
| --- | --- | --- |
| `ProjectionPort` | touch, focus, mic blocks | connection state, video frames, audio blocks, mic enablement |
| `ConnectivityPort` | scan/pair/connect actions | connectivity status and device list |
| `SettingsPort` | list/read/write schemas and values | settings snapshots and validation results |
| `AudioControlPort` | volume action | audio state |
| `DiagnosticsPort` | filter/select logs | log entries and provider health |
| `ApplicationPort` | close request | capability/health changes |

A port is injected into a `QtShellController` at startup, never looked up by module name. Every port has a `Null...Adapter` for standalone mode, an `InProcess...Adapter` for embedded thread mode, and, if full mode still requires it, an HTTP/ZMQ adapter that preserves existing externally visible APIs. Dependency direction is strictly:

```text
Qt widgets -> QtShellController -> contracts <- adapters <- providers/modules
```

The controller may depend on contracts but never on `channel_manager`, `media_server`, `connectivity_manager`, `config_manager`, `BusClient`, or `aiohttp`. Providers similarly never import Qt. This retains substitutability and avoids a large `HeadUnitService` object that would merely disguise hard coupling.

## Implementation plan

### 1. Establish the boundary and boot model

1. Add `backend/contracts/` with immutable event/value dataclasses and small `typing.Protocol` ports listed above. Define explicit availability and error states; do not encode absent services as empty dictionaries or failed HTTP calls.
2. Add an application composition root, separate from `Qt6GuiModule`, that parses `--mode qt-standalone|qt-embedded|full` and creates `QApplication` on the process main thread.
3. Integrate the existing `qasync` dependency so Qt and asyncio share one main event loop. Replace the manual 16 ms `processEvents()` polling loop.
4. Make the direct kiosk script call the standalone composition root. It must not instantiate `BaseBackendModule`, connect ZMQ, allocate SHM, or wait for `system.start`.
5. Keep `Qt6GuiModule` temporarily as a compatibility adapter for full/process mode; make it construct the same shell/controller with remote adapters rather than own the widgets' lifecycle.

### 2. Make the Qt UI transport-agnostic

1. Remove `urllib` and `QThread` network classes from Qt widgets. Replace direct network operations with intent signals: `scan_requested`, `volume_action_requested`, `settings_save_requested`, `log_filter_changed`, and `focus_requested`.
2. Add controllers for connectivity, settings, volume, diagnostics, and projection. They subscribe to their injected ports and update widgets only on the Qt main thread.
3. Wire all currently unused signals through these controllers and implement a bounded in-memory log model for the logs drawer.
4. In standalone mode inject null/local adapters: persist Qt-owned preferences through a small local settings store, disable unsupported controls with a clear unavailable state, and avoid retry loops or background workers for absent providers.
5. Add capability-driven widget visibility/enabling so a future provider can be added without modifying the widget or the controller's public API.

### 3. Build the embedded thread-mode runtime

1. Add `InProcessBus` as an implementation of a transport protocol, with owner-loop registration, bounded per-subscriber queues, event classes, observability, and deterministic shutdown.
2. Add a provider registry at the composition root. Adapters register against contracts, not names or module imports. The registry is the only place allowed to know which provider implementation is selected.
3. Migrate lifecycle, configuration, focus, touch, volume, connectivity state, and logs to ports first. Keep existing ZMQ/HTTP adapters active in full mode during this transition.
4. Move Qt to the main thread and run TCP, channel, connectivity, and media providers as background tasks/threads only where their external libraries require blocking ownership. Use queued delivery into the main Qt loop.
5. Define a single browser-media owner before changing media delivery. The current `channel_manager` and `media_server` both broadcast media, so embedded mode must choose one source of truth.

### 4. Replace the thread-mode media hot path

1. Define `EncodedVideoFrame`, `DecodedVideoFrame`, and `AudioBlock` contracts with sequence number, timestamp, format, dimensions/rate, and immutable payload ownership.
2. Change TCP -> channel manager -> media server to pass frame references through bounded queues in embedded mode. Do not write these frames to SHM, hex, JSON, or base64; retain the current process-mode adapter separately.
3. Use a latest-only video queue, a timestamped bounded audio queue, and metrics for drops, queue age, and discontinuities. Backpressure policy must be part of the contract.
4. Update the Qt renderer to consume explicit frame formats, retain only the latest displayable frame, and use texture sub-image uploads when dimensions remain stable.
5. Move AAC decode/resample off the Qt main thread and reduce/configure the `QAudioSink` buffer from the current 250 ms only after measuring device behavior.

### 5. Verify and retire compatibility paths deliberately

1. Unit-test each controller with null and fake port adapters; no test should require a broker, HTTP server, or Qt network worker to validate view behavior.
2. Add Qt offscreen tests that prove standalone boot reaches a visible window, exits cleanly, and never opens ZMQ/SHM/HTTP connections.
3. Add embedded integration tests for touch/focus, mic enablement, video latest-frame drops, audio ordering, provider absence, and shutdown.
4. Add full-mode regression tests for existing ZMQ/HTTP/SHM adapters so process isolation remains an intentional supported configuration.
5. Benchmark end-to-end phone-frame-to-Qt-submit latency, CPU, memory, and frame loss in full versus embedded mode. Set acceptance budgets only after collecting a representative device baseline.

### Delivery order and risk

Deliver steps 1 and 2 first; they make Qt independently bootable and remove the hard-coded transport assumptions without changing Android Auto media behavior. Step 3 then gives thread mode a real in-memory control plane. Step 4 is the high-value, higher-risk media conversion and should be guarded by the metrics and test coverage from step 5. Do not merge the mode-specific direct path by sprinkling `if threaded` checks through widgets or providers; select adapters once in the composition root.

## Initial implementation status

The first delivery slice now exists in the repository:

- `backend/contracts/` defines transport-free ports and immutable display-state objects.
- `backend/shared/inprocess_bus.py` provides a typed bounded in-process bus with explicit wildcard syntax, loop-owned dispatch, drop accounting, and focused tests.
- `backend/modules/qt6_gui/standalone.py` starts the Qt shell directly, without the backend module lifecycle, ZMQ, SHM, or `system.start` dependency. `scripts/launch_qt_kiosk.sh` now targets this entry point.
- `application/` contains a controller plus null/local standalone adapters. The standalone composition disables the existing widget-owned network workers and routes widget intents through ports.
- Existing full-mode ZMQ/HTTP/SHM operation remains unchanged apart from the Qt widgets exposing intent signals for the new controller path.

The remaining work is the embedded composition root, real in-process provider adapters, and replacement of the thread-mode media SHM/JSON path. Those pieces should be added behind the contracts rather than by changing Qt widgets to import backend modules.
