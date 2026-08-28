"""Independent native Qt shell entry point with no backend lifecycle dependency."""

from __future__ import annotations

import argparse
import sys

from PyQt6.QtWidgets import QApplication

from backend.modules.qt6_gui.application.controller import QtShellController
from backend.modules.qt6_gui.application.null_adapters import (
    LocalSettingsAdapter,
    NullAudioControlAdapter,
    NullConnectivityAdapter,
    NullDiagnosticsAdapter,
    NullProjectionAdapter,
)
from backend.modules.qt6_gui.ui.main_window import MainWindow


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NemoHeadUnit standalone Qt shell")
    parser.add_argument("--fullscreen", action="store_true", help="Start the shell fullscreen")
    options = parser.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(service_enabled=False)
    controller = QtShellController(
        window,
        NullProjectionAdapter(),
        NullConnectivityAdapter(),
        LocalSettingsAdapter(),
        NullAudioControlAdapter(),
        NullDiagnosticsAdapter(),
    )
    controller.start()
    window.close_app_requested.connect(app.quit)
    if options.fullscreen:
        window.showFullScreen()
    else:
        window.show()
    result = app.exec()
    controller.close()
    return result


if __name__ == "__main__":
    raise SystemExit(run())
