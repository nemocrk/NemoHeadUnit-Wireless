"""
NemoHeadUnit-Wireless v2 — bluetooth_ui module

Standalone PyQt6 window to trigger and monitor Bluetooth pairing.
Useful for validating the v2 IPC infrastructure end-to-end.

Module contract:
  Name        : bluetooth_ui
  Subscribes  : system.start
                system.stop
                bluetooth.device.found        {address, name, rssi}
                bluetooth.discovery.completed {devices: [...]}
                bluetooth.pairing.pin         {device_address, pin}
                bluetooth.pairing.completed   {device_address}
                bluetooth.pairing.failed      {device_address, error}
  Publishes   : bluetooth.discover            {duration_sec: int}
                bluetooth.pair                {device_address: str}
                bluetooth.confirm_pairing     {device_address: str, pin: str}
"""

import sys
import threading
from pathlib import Path

_HERE    = Path(__file__).parent
_MODULES = _HERE.parent
_V2      = _MODULES.parent

if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))
if str(_MODULES) not in sys.path:
    sys.path.insert(0, str(_MODULES))

from PyQt6.QtCore import Qt, QMetaObject, Q_ARG, pyqtSlot          # noqa: E402
from PyQt6.QtWidgets import (                                        # noqa: E402
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QListWidget, QListWidgetItem, QLabel, QStatusBar,
    QDialog, QDialogButtonBox, QLineEdit, QFormLayout, QMessageBox,
)

from shared.bus_client import BusClient   # noqa: E402
from shared.logger import get_logger      # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "bluetooth_ui"

log = get_logger(MODULE_NAME)
bus = BusClient(module_name=MODULE_NAME)

# ---------------------------------------------------------------------------
# PIN confirmation dialog
# ---------------------------------------------------------------------------

class PinDialog(QDialog):
    def __init__(self, device_address: str, pin: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Conferma PIN Bluetooth")
        self.setMinimumWidth(320)

        self._address = device_address

        layout = QFormLayout(self)
        layout.addRow("Dispositivo:", QLabel(device_address))
        layout.addRow("PIN:", QLabel(f"<b>{pin}</b>"))

        self._pin_input = QLineEdit()
        self._pin_input.setPlaceholderText("Reinserisci il PIN per confermare")
        layout.addRow("Conferma PIN:", self._pin_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def confirmed_pin(self) -> str:
        return self._pin_input.text().strip()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class BluetoothPairingWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bluetooth Pairing Monitor — NemoHeadUnit v2")
        self.setMinimumSize(520, 400)

        self._devices: dict[str, dict] = {}  # address → {name, rssi}
        self._pending_pin_address: str = ""

        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        # — top bar ——————————————————————————————————————————————————————————
        top = QHBoxLayout()
        self._btn_scan = QPushButton("🔍  Avvia Ricerca (10s)")
        self._btn_scan.setMinimumHeight(40)
        self._btn_scan.clicked.connect(self._on_scan_clicked)

        self._btn_pair = QPushButton("🔗  Pair dispositivo")
        self._btn_pair.setMinimumHeight(40)
        self._btn_pair.setEnabled(False)
        self._btn_pair.clicked.connect(self._on_pair_clicked)

        top.addWidget(self._btn_scan, stretch=2)
        top.addWidget(self._btn_pair, stretch=1)
        root.addLayout(top)

        # — device list ——————————————————————————————————————————————————————
        root.addWidget(QLabel("Dispositivi trovati:"))
        self._device_list = QListWidget()
        self._device_list.itemSelectionChanged.connect(self._on_selection_changed)
        root.addWidget(self._device_list, stretch=1)

        # — status bar ———————————————————————————————————————————————————————
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("In attesa di system.start…")

    # -----------------------------------------------------------------------
    # Qt slots (always called on the main thread via QMetaObject.invokeMethod)
    # -----------------------------------------------------------------------

    @pyqtSlot(str)
    def set_status(self, message: str):
        self._status.showMessage(message)

    @pyqtSlot(str, str, int)
    def add_device(self, address: str, name: str, rssi: int):
        if address in self._devices:
            return
        self._devices[address] = {"name": name, "rssi": rssi}
        label = f"{name or '(sconosciuto)'}  [{address}]  RSSI: {rssi} dBm"
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, address)
        self._device_list.addItem(item)

    @pyqtSlot(str, str)
    def show_pin_dialog(self, address: str, pin: str):
        self._pending_pin_address = address
        self.set_status(f"PIN richiesto per {address}: {pin}")
        dlg = PinDialog(address, pin, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            confirmed = dlg.confirmed_pin()
            bus.publish("bluetooth.confirm_pairing", {
                "device_address": address,
                "pin": confirmed,
            })
            self.set_status(f"PIN confermato per {address}")
        else:
            self.set_status("Pairing annullato dall'utente")

    @pyqtSlot(str)
    def on_pairing_completed(self, address: str):
        self.set_status(f"✅  Pairing completato con {address}")
        QMessageBox.information(self, "Pairing completato", f"Connesso a:\n{address}")

    @pyqtSlot(str, str)
    def on_pairing_failed(self, address: str, error: str):
        self.set_status(f"❌  Pairing fallito con {address}: {error}")
        QMessageBox.warning(self, "Pairing fallito", f"Dispositivo: {address}\nErrore: {error}")

    # -----------------------------------------------------------------------
    # User interactions
    # -----------------------------------------------------------------------

    def _on_scan_clicked(self):
        self._devices.clear()
        self._device_list.clear()
        self._btn_pair.setEnabled(False)
        self.set_status("Ricerca dispositivi in corso…")
        bus.publish("bluetooth.discover", {"duration_sec": 10})

    def _on_pair_clicked(self):
        selected = self._device_list.currentItem()
        if not selected:
            return
        address = selected.data(Qt.ItemDataRole.UserRole)
        self.set_status(f"Avvio pairing con {address}…")
        bus.publish("bluetooth.pair", {"device_address": address})

    def _on_selection_changed(self):
        self._btn_pair.setEnabled(bool(self._device_list.currentItem()))


# ---------------------------------------------------------------------------
# Module-level window reference (created after QApplication)
# ---------------------------------------------------------------------------

_window: BluetoothPairingWindow | None = None


def _invoke(slot_name: str, *args):
    """Thread-safe call from the ZMQ recv thread to the Qt main thread."""
    if _window is None:
        return
    type_map = {str: "QString", int: "int"}
    q_args = [Q_ARG(type(a), a) for a in args]
    QMetaObject.invokeMethod(_window, slot_name, Qt.ConnectionType.QueuedConnection, *q_args)


# ---------------------------------------------------------------------------
# Bus handlers (called from ZMQ recv thread — must NOT touch Qt directly)
# ---------------------------------------------------------------------------

def on_system_start(topic: str, payload: dict) -> None:
    log.info("system.start received")
    _invoke("set_status", "Sistema pronto. Avvia una ricerca Bluetooth.")


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop received — exiting")
    _invoke("set_status", "Sistema in arresto…")
    bus.stop()
    if _app:
        _app.quit()


def on_device_found(topic: str, payload: dict) -> None:
    address = payload.get("address", "")
    name    = payload.get("name", "")
    rssi    = int(payload.get("rssi", 0))
    log.debug(f"Device found: {address} {name}")
    _invoke("add_device", address, name, rssi)


def on_discovery_completed(topic: str, payload: dict) -> None:
    count = len(payload.get("devices", []))
    _invoke("set_status", f"Ricerca completata. {count} dispositivo/i trovato/i.")


def on_pairing_pin(topic: str, payload: dict) -> None:
    address = payload.get("device_address", "")
    pin     = payload.get("pin", "")
    _invoke("show_pin_dialog", address, pin)


def on_pairing_completed(topic: str, payload: dict) -> None:
    address = payload.get("device_address", "")
    _invoke("on_pairing_completed", address)


def on_pairing_failed(topic: str, payload: dict) -> None:
    address = payload.get("device_address", "")
    error   = payload.get("error", "errore sconosciuto")
    _invoke("on_pairing_failed", address, error)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_app: QApplication | None = None


def run() -> None:
    global _app, _window

    bus.subscribe("system.start",               on_system_start)
    bus.subscribe("system.stop",                on_system_stop)
    bus.subscribe("bluetooth.device.found",     on_device_found)
    bus.subscribe("bluetooth.discovery.completed", on_discovery_completed)
    bus.subscribe("bluetooth.pairing.pin",      on_pairing_pin)
    bus.subscribe("bluetooth.pairing.completed",on_pairing_completed)
    bus.subscribe("bluetooth.pairing.failed",   on_pairing_failed)

    # Start the ZMQ receive loop in a background thread so Qt owns the main thread
    bus_thread = threading.Thread(target=lambda: bus.start(blocking=True), daemon=True)
    bus_thread.start()

    _app = QApplication(sys.argv)
    _window = BluetoothPairingWindow()
    _window.show()

    log.info("bluetooth_ui window open")
    sys.exit(_app.exec())


if __name__ == "__main__":
    run()
