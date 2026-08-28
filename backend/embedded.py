"""Embedded headunit composition: Qt on main thread, providers on shared bus."""

from __future__ import annotations

import threading
import time

from modules.channel_manager.main import ChannelManagerModule
from modules.config_manager.main import ConfigManagerModule
from modules.connectivity_manager.main import ConnectivityManagerModule
from modules.media_server.main import MediaServerModule
from modules.qt6_gui.main import Qt6GuiModule
from modules.tcp_server.main import TCPServerModule
from shared.inprocess_bus import InProcessBus
from shared.runtime import clear_inprocess_runtime, configure_inprocess_runtime


_PROVIDER_CLASSES = (
    ConfigManagerModule,
    ConnectivityManagerModule,
    TCPServerModule,
    ChannelManagerModule,
    MediaServerModule,
)


def run_embedded() -> int:
    """Run the selected provider set with the Qt module owned by the main thread."""
    bus = InProcessBus()
    configure_inprocess_runtime(bus)
    providers = [provider_cls() for provider_cls in _PROVIDER_CLASSES]
    threads = [
        threading.Thread(target=provider.run_main, daemon=True, name=f"embedded_{provider.name}")
        for provider in providers
    ]
    for thread in threads:
        thread.start()

    def boot() -> None:
        # Modules register lifecycle subscriptions before their first await. The
        # staged start preserves dependency order without a broker round trip.
        time.sleep(0.75)
        for priority in (1, 3, 4, 5):
            bus.publish("system.start", {"priority": priority})
            time.sleep(0.5)

    threading.Thread(target=boot, daemon=True, name="embedded_boot").start()
    qt_module = Qt6GuiModule()
    try:
        qt_module.run_main()
    except SystemExit:
        pass
    finally:
        bus.publish("system.stop", {"reason": "embedded_exit"})
        for thread in threads:
            thread.join(timeout=2.0)
        bus.close()
        clear_inprocess_runtime()
    return 0
