"""
NemoHeadUnit-Wireless v2 — bluetooth_ui module

On-request PyQt6 panel for Bluetooth pairing and device management.
Registered as an on_request widget via the ui_shell compositor.

Module contract:
  Name        : bluetooth_ui
  Priority    : 4  (on_request widget — ui_shell guaranteed ready)
  Subscribes  : system.readytostart
                system.start
                system.stop
                ui.shell.ready              → {} (triggers registration)
                ui.widget.geometry          → {name, x, y, w, h, dpi_factor}
                ui.module.open              → {name}  (show panel)
                ui.module.close             → {name}  (hide panel)
                input.event.bluetooth_ui    → {type, x, y, ...}
                bluetooth_manager.device.found        {address, name, rssi}
                bluetooth_manager.discovery.completed {devices: [...]}
                bluetooth_manager.pairing.pin         {device_address, pin}
                bluetooth_manager.pairing.completed   {device_address}
                bluetooth_manager.pairing.failed      {device_address, error}
                bluetooth_manager.paired.devices      {devices: [{address, name, connected, trusted}]}
                bluetooth_manager.paired.connected    {device_address}
                bluetooth_manager.paired.disconnected {device_address}
                bluetooth_manager.paired.removed      {device_address}
                bluetooth_manager.paired.failed       {device_address, error}
  Publishes   : system.module_ready            {name, priority}
                system.ready                   {name, priority}
                ui.widget.register             {name, z_order, dock, on_request, menu_order, icon}
                ui.widget.unregister           {name}
                bluetooth_manager.discover            {duration_sec: int}
                bluetooth_manager.pair                {device_address: str}
                bluetooth_manager.confirm_pairing     {device_address: str, pin: str}
                bluetooth_manager.paired.list         {}
                bluetooth_manager.paired.connect      {device_address: str}
                bluetooth_manager.paired.disconnect   {device_address: str}
                bluetooth_manager.paired.remove       {device_address: str}
                bluetooth_manager.try_autoconnect     {}

UI Architecture compliance:
  - Frameless, transparent, Tool window (never touches z-order directly)
  - Registered as on_request=True; floating_menu_ui discovers it and adds arc icon
  - All geometry driven by ui.widget.geometry from ui_shell
  - Input received via input.event.bluetooth_ui (routed by ui_shell/input_trap)
  - Design tokens: DM Sans typography, --color-surface palette
"""

import sys
import threading
from pathlib import Path
import time

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
    QFrame,
)

from shared.bus_client import BusClient             # noqa: E402
from shared.logger import get_logger    # noqa: E402

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------

MODULE_NAME = "bluetooth_ui"
PRIORITY    = 4  # on_request widget (ui_shell at priority 2 is guaranteed ready)

bus = BusClient(module_name=MODULE_NAME)
log = get_logger(MODULE_NAME, bus=bus)

# ---------------------------------------------------------------------------
# Design system tokens (UI_DESIGN_SYSTEM.md)
# ---------------------------------------------------------------------------
# Colors applied in Qt stylesheet below
_COLOR_BG        = "#141414"   # --color-bg
_COLOR_SURFACE   = "#1c1c1c"   # --color-surface
_COLOR_SURFACE_2 = "#242424"   # --color-surface-2
_COLOR_BORDER    = "rgba(255,255,255,0.06)"  # --color-border
_COLOR_TEXT      = "#f0ece4"   # --color-text
_COLOR_TEXT_MUTED = "#8a8680"  # --color-text-muted
_COLOR_ACCENT    = "#c8b89a"   # --color-accent
_COLOR_DANGER    = "#c0392b"   # --color-danger
_COLOR_SUCCESS   = "#4a7c59"   # --color-success

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

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class BluetoothPairingWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # ── UI Architecture compliance: frameless transparent tool window ──
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool                 # hidden from taskbar
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Bluetooth Pairing — NemoHeadUnit v2")
        self.hide()  # hidden until ui_shell sends ui.module.open

        self._devices: dict[str, dict] = {}
        self._pending_pin_address: str = ""

        self._build_ui()
        self._apply_design_tokens()

    # ── UI Shell geometry contract ────────────────────────────────────────

    @pyqtSlot(int, int, int, int)
    def apply_geometry_slot(self, x: int, y: int, w: int, h: int) -> None:
        """Called on the Qt thread via invokeMethod from on_widget_geometry."""
        self.setGeometry(x, y, w, h)
        self.show()
        self.raise_()

    @pyqtSlot(bool)
    def set_visible_slot(self, visible: bool) -> None:
        """Show or hide panel as requested by floating_menu_ui via ZMQ."""
        if visible:
            self.show()
            self.raise_()
        else:
            self.hide()

    # ── Design tokens ─────────────────────────────────────────────────────

    def _apply_design_tokens(self) -> None:
        """Apply DM Sans typography and design-system color palette."""
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                font-family: 'DM Sans', sans-serif;
                font-size: 14px;
                background-color: {_COLOR_SURFACE};
                color: {_COLOR_TEXT};
            }}
            QPushButton {{
                background-color: {_COLOR_SURFACE_2};
                color: {_COLOR_TEXT};
                border: 1px solid {_COLOR_BORDER};
                border-radius: 8px;
                padding: 6px 12px;
                font-family: 'DM Sans', sans-serif;
            }}
            QPushButton:hover {{
                background-color: {_COLOR_ACCENT};
                color: {_COLOR_BG};
            }}
            QPushButton:disabled {{
                color: {_COLOR_TEXT_MUTED};
            }}
            QListWidget {{
                background-color: {_COLOR_BG};
                border: 1px solid {_COLOR_BORDER};
                border-radius: 8px;
                color: {_COLOR_TEXT};
                font-family: 'DM Sans', sans-serif;
            }}
            QLabel {{
                color: {_COLOR_TEXT_MUTED};
                font-family: 'DM Sans', sans-serif;
            }}
            QStatusBar {{
                background-color: {_COLOR_SURFACE};
                color: {_COLOR_TEXT_MUTED};
            }}
        """)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(8)
        root.setContentsMargins(12, 12, 12, 12)

        # ── Discovery / Pairing section ──────────────────────────────────
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

        root.addWidget(QLabel("Dispositivi trovati:"))
        self._device_list = QListWidget()
        self._device_list.itemSelectionChanged.connect(self._on_selection_changed)
        root.addWidget(self._device_list, stretch=1)

        # ── Separator ────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(sep)

        # ── Paired devices section ───────────────────────────────────────
        paired_header = QHBoxLayout()
        paired_header.addWidget(QLabel("Dispositivi accoppiati:"))
        paired_header.addStretch()

        self._btn_refresh_paired = QPushButton("🔄  Aggiorna")
        self._btn_refresh_paired.setMinimumHeight(32)
        self._btn_refresh_paired.clicked.connect(self._on_refresh_paired_clicked)
        paired_header.addWidget(self._btn_refresh_paired)

        self._btn_autoconnect = QPushButton("⚡  Riavvia Autoconnect")
        self._btn_autoconnect.setMinimumHeight(32)
        self._btn_autoconnect.clicked.connect(self._on_autoconnect_clicked)
        paired_header.addWidget(self._btn_autoconnect)

        root.addLayout(paired_header)

        self._paired_list = QListWidget()
        self._paired_list.itemSelectionChanged.connect(self._on_paired_selection_changed)
        root.addWidget(self._paired_list, stretch=1)

        paired_actions = QHBoxLayout()
        self._btn_connect_paired = QPushButton("🔌  Connetti")
        self._btn_connect_paired.setMinimumHeight(36)
        self._btn_connect_paired.setEnabled(False)
        self._btn_connect_paired.clicked.connect(self._on_connect_paired_clicked)

        self._btn_disconnect_paired = QPushButton("⛔  Disconnetti")
        self._btn_disconnect_paired.setMinimumHeight(36)
        self._btn_disconnect_paired.setEnabled(False)
        self._btn_disconnect_paired.clicked.connect(self._on_disconnect_paired_clicked)

        self._btn_remove_paired = QPushButton("🗑  Rimuovi")
        self._btn_remove_paired.setMinimumHeight(36)
        self._btn_remove_paired.setEnabled(False)
        self._btn_remove_paired.clicked.connect(self._on_remove_paired_clicked)

        paired_actions.addWidget(self._btn_connect_paired)
        paired_actions.addWidget(self._btn_disconnect_paired)
        paired_actions.addWidget(self._btn_remove_paired)
        root.addLayout(paired_actions)

        # ── Status bar ───────────────────────────────────────────────────
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("In attesa di system.start…")

    # ── Generic slots ────────────────────────────────────────────────────

    @pyqtSlot(str)
    def set_status(self, message: str):
        self._status.showMessage(message)

    # ── Discovery / Pairing slots ─────────────────────────────────────────

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
            bus.publish("bluetooth_manager.confirm_pairing", {
                "device_address": address,
                "pin": pin,
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

    # ── Paired devices slots ──────────────────────────────────────────────

    @pyqtSlot(str)  # JSON-serialised list passed as str via invokeMethod
    def refresh_paired_list(self, devices_repr: str):
        """Rebuild the paired list widget from a repr string of list[dict]."""
        import ast
        try:
            devices: list[dict] = ast.literal_eval(devices_repr)
        except Exception:
            devices = []
        self._paired_list.clear()
        for dev in devices:
            address   = dev.get("address", "")
            name      = dev.get("name", "") or "(sconosciuto)"
            connected = dev.get("connected", False)
            trusted   = dev.get("trusted", False)
            state     = "🟢 connesso" if connected else "⚪ non connesso"
            trust_tag = " ✓trusted" if trusted else ""
            label     = f"{name}  [{address}]  {state}{trust_tag}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, address)
            item.setData(Qt.ItemDataRole.UserRole + 1, connected)
            self._paired_list.addItem(item)
        self._update_paired_buttons()

    @pyqtSlot(str)
    def on_paired_connected(self, address: str):
        self.set_status(f"🟢  Connesso a {address}")
        self._refresh_item_state(address, connected=True)
        self._update_paired_buttons()

    @pyqtSlot(str)
    def on_paired_disconnected(self, address: str):
        self.set_status(f"⚪  Disconnesso da {address}")
        self._refresh_item_state(address, connected=False)
        self._update_paired_buttons()

    @pyqtSlot(str)
    def on_paired_removed(self, address: str):
        self.set_status(f"🗑  Rimosso {address}")
        for i in range(self._paired_list.count()):
            item = self._paired_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == address:
                self._paired_list.takeItem(i)
                break
        self._update_paired_buttons()

    @pyqtSlot(str, str)
    def on_paired_failed(self, address: str, error: str):
        self.set_status(f"❌  Operazione fallita su {address}: {error}")

    # ── Internal helpers ──────────────────────────────────────────────────

    def _refresh_item_state(self, address: str, connected: bool) -> None:
        """Update the label and UserRole+1 flag of a paired list item in-place."""
        for i in range(self._paired_list.count()):
            item = self._paired_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == address:
                state = "🟢 connesso" if connected else "⚪ non connesso"
                # Rebuild label preserving name portion
                text  = item.text()
                # Replace the state suffix only
                parts = text.rsplit("  ", 1)
                new_label = f"{parts[0]}  {state}" if len(parts) == 2 else text
                item.setText(new_label)
                item.setData(Qt.ItemDataRole.UserRole + 1, connected)
                break

    def _update_paired_buttons(self) -> None:
        item = self._paired_list.currentItem()
        if not item:
            self._btn_connect_paired.setEnabled(False)
            self._btn_disconnect_paired.setEnabled(False)
            self._btn_remove_paired.setEnabled(False)
            return
        connected = bool(item.data(Qt.ItemDataRole.UserRole + 1))
        self._btn_connect_paired.setEnabled(not connected)
        self._btn_disconnect_paired.setEnabled(connected)
        self._btn_remove_paired.setEnabled(True)

    def _selected_paired_address(self) -> str:
        item = self._paired_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    # ── Button handlers ───────────────────────────────────────────────────

    def _on_scan_clicked(self):
        self._devices.clear()
        self._device_list.clear()
        self._btn_pair.setEnabled(False)
        self.set_status("Ricerca dispositivi in corso…")
        bus.publish("bluetooth_manager.discover", {"duration_sec": 10})

    def _on_pair_clicked(self):
        selected = self._device_list.currentItem()
        if not selected:
            return
        address = selected.data(Qt.ItemDataRole.UserRole)
        self.set_status(f"Avvio pairing con {address}…")
        bus.publish("bluetooth_manager.pair", {"device_address": address})

    def _on_selection_changed(self):
        self._btn_pair.setEnabled(bool(self._device_list.currentItem()))

    def _on_paired_selection_changed(self):
        self._update_paired_buttons()

    def _on_refresh_paired_clicked(self):
        self.set_status("Aggiornamento lista dispositivi accoppiati…")
        bus.publish("bluetooth_manager.paired.list", {})

    def _on_autoconnect_clicked(self):
        self.set_status("Riavvio autoconnect richiesto…")
        bus.publish("bluetooth_manager.try_autoconnect", {})

    def _on_connect_paired_clicked(self):
        address = self._selected_paired_address()
        if not address:
            return
        self.set_status(f"Connessione a {address}…")
        bus.publish("bluetooth_manager.paired.connect", {"device_address": address})

    def _on_disconnect_paired_clicked(self):
        address = self._selected_paired_address()
        if not address:
            return
        self.set_status(f"Disconnessione da {address}…")
        bus.publish("bluetooth_manager.paired.disconnect", {"device_address": address})

    def _on_remove_paired_clicked(self):
        address = self._selected_paired_address()
        if not address:
            return
        reply = QMessageBox.question(
            self,
            "Rimuovi dispositivo",
            f"Rimuovere il dispositivo\n{address}\ndall'elenco accoppiati?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.set_status(f"Rimozione {address}…")
            bus.publish("bluetooth_manager.paired.remove", {"device_address": address})


_window: BluetoothPairingWindow | None = None

# Track whether ui.shell.ready has been received
_shell_ready: bool = False


def _invoke(slot_name: str, *args):
    if _window is None:
        return
    q_args = [Q_ARG(type(a), a) for a in args]
    QMetaObject.invokeMethod(_window, slot_name, Qt.ConnectionType.QueuedConnection, *q_args)


def _register() -> None:
    """Publish ui.widget.register — on_request so floating_menu_ui shows arc icon."""
    bus.publish("ui.widget.register", {
        "name":       MODULE_NAME,
        "z_order":    2,
        "dock":       "center",
        "on_request": True,
        "menu_order": 1,
        "icon":       "📱",  # Bluetooth phone glyph for arc menu
        "width":      480,
        "height":     560,
    })
    log.info("ui.widget.register published (on_request)")


# ---------------------------------------------------------------------------
# Bus handlers
# ---------------------------------------------------------------------------

def on_system_readytostart() -> None:
    log.info(f"system.readytostart received — announcing priority {PRIORITY}")
    bus.publish("system.module_ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })


def on_ui_shell_ready(topic: str, payload: dict) -> None:
    global _shell_ready
    _shell_ready = True
    log.info("ui.shell.ready received — registering bluetooth_ui")
    _register()


def on_widget_geometry(topic: str, payload: dict) -> None:
    """Called from bus thread — must NOT touch Qt directly."""
    if payload.get("name") != MODULE_NAME:
        return
    x, y, w, h = int(payload["x"]), int(payload["y"]), int(payload["w"]), int(payload["h"])
    log.info(f"Geometry received: x={x} y={y} w={w} h={h}")
    _invoke("apply_geometry_slot", x, y, w, h)


def on_module_open(topic: str, payload: dict) -> None:
    if payload.get("name") != MODULE_NAME:
        return
    log.info("ui.module.open received — showing bluetooth_ui")
    _invoke("set_visible_slot", True)
    # Auto-populate paired list on open
    bus.publish("bluetooth_manager.paired.list", {})


def on_module_close(topic: str, payload: dict) -> None:
    if payload.get("name") != MODULE_NAME:
        return
    log.info("ui.module.close received — hiding bluetooth_ui")
    _invoke("set_visible_slot", False)


def on_input_event(topic: str, payload: dict) -> None:
    """Input routed via ui_shell input_trap — reconstruct Qt synthetic events."""
    if _window is None:
        return
    ev_type = payload.get("type")
    x = int(payload.get("x", 0))
    y = int(payload.get("y", 0))
    # For this panel, native Qt interaction via show() is sufficient;
    # input_trap routing arrives here for logging/future use.
    log.debug(f"input.event received: type={ev_type} x={x} y={y}")


def on_system_start(topic: str, payload: dict) -> None:
    if payload.get("priority") != PRIORITY:
        return

    log.info(f"system.start priority={PRIORITY} — bluetooth_ui ready")
    _invoke("set_status", "Sistema pronto. Avvia una ricerca Bluetooth.")

    bus.subscribe("bluetooth_manager.device.found",        on_device_found)
    bus.subscribe("bluetooth_manager.discovery.completed", on_discovery_completed)
    bus.subscribe("bluetooth_manager.pairing.pin",         on_pairing_pin)
    bus.subscribe("bluetooth_manager.pairing.completed",   on_pairing_completed)
    bus.subscribe("bluetooth_manager.pairing.failed",      on_pairing_failed)
    bus.subscribe("bluetooth_manager.paired.devices",      on_paired_devices)
    bus.subscribe("bluetooth_manager.paired.connected",    on_paired_connected)
    bus.subscribe("bluetooth_manager.paired.disconnected", on_paired_disconnected)
    bus.subscribe("bluetooth_manager.paired.removed",      on_paired_removed)
    bus.subscribe("bluetooth_manager.paired.failed",       on_paired_failed)

    bus.publish("system.ready", {
        "name":     MODULE_NAME,
        "priority": PRIORITY,
    })
    log.info("system.ready published — bluetooth_ui online")

    if _shell_ready:
        log.info("ui.shell.ready already received — registering immediately")
        _register()


def on_system_stop(topic: str, payload: dict) -> None:
    log.info("system.stop received — exiting")
    _invoke("set_status", "Sistema in arresto…")
    bus.stop()
    # _app.quit() must be called from the Qt main thread.
    # Using invokeMethod with QueuedConnection ensures it is dispatched
    # onto the event loop, regardless of which thread receives system.stop.
    if _app:
        QMetaObject.invokeMethod(_app, "quit", Qt.ConnectionType.QueuedConnection)


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


def on_paired_devices(topic: str, payload: dict) -> None:
    devices = payload.get("devices", [])
    log.debug(f"paired.devices received: {len(devices)} device(s)")
    _invoke("refresh_paired_list", repr(devices))


def on_paired_connected(topic: str, payload: dict) -> None:
    address = payload.get("device_address", "")
    log.info(f"paired.connected: {address}")
    _invoke("on_paired_connected", address)


def on_paired_disconnected(topic: str, payload: dict) -> None:
    address = payload.get("device_address", "")
    log.info(f"paired.disconnected: {address}")
    _invoke("on_paired_disconnected", address)


def on_paired_removed(topic: str, payload: dict) -> None:
    address = payload.get("device_address", "")
    log.info(f"paired.removed: {address}")
    _invoke("on_paired_removed", address)


def on_paired_failed(topic: str, payload: dict) -> None:
    address = payload.get("device_address", "")
    error   = payload.get("error", "errore sconosciuto")
    log.warning(f"paired.failed: {address} — {error}")
    _invoke("on_paired_failed", address, error)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_app: QApplication | None = None


def run() -> None:
    global _app, _window

    bus.subscribe("system.readytostart",              on_system_readytostart)
    bus.subscribe("system.start",                     on_system_start)
    bus.subscribe("system.stop",                      on_system_stop)
    bus.subscribe("ui.shell.ready",                   on_ui_shell_ready)
    bus.subscribe("ui.widget.geometry",               on_widget_geometry)
    bus.subscribe("ui.module.open",                   on_module_open)
    bus.subscribe("ui.module.close",                  on_module_close)
    bus.subscribe(f"input.event.{MODULE_NAME}",       on_input_event)

    bus_thread = bus.start(blocking=False)
    time.sleep(0.05)
    on_system_readytostart()

    _app = QApplication(sys.argv)
    _window = BluetoothPairingWindow()
    # Window is hidden by default; ui_shell will send ui.module.open
    # when the user taps the arc menu icon.

    log.info("bluetooth_ui window created (hidden, awaiting ui.module.open)")
    try:
        bus_thread.join()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
