import sys
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QPainter, QColor, QFont

class TransparentWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |      # Niente bordi/titlebar
            Qt.WindowType.WindowStaysOnTopHint |     # Sempre in primo piano
            Qt.WindowType.Tool                       # Non appare in taskbar
        )

        # Abilita trasparenza reale del canale alpha
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Dimensione fissa → non ridimensionabile
        self.setFixedSize(400, 200)

        self._drag_pos = QPoint()
        self._build_ui()
        self._center_on_screen()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        label = QLabel("👻 Finestra trasparente\nClicca e trascina per spostarla\nTasto destro → chiudi")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        label.setStyleSheet("color: white;")
        self.setLayout(layout)
        layout.addWidget(label)

    def _center_on_screen(self):
        screen = QApplication.primaryScreen().geometry()
        self.move(
            (screen.width()  - self.width())  // 2,
            (screen.height() - self.height()) // 2,
        )

    # ── Disegna lo sfondo con trasparenza parziale ──────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Sfondo semi-trasparente con angoli arrotondati
        painter.setBrush(QColor(30, 30, 30, 180))   # RGBA: alpha 180/255 ≈ 70%
        painter.setPen(QColor(100, 100, 255, 200))  # Bordo colorato opzionale
        painter.drawRoundedRect(self.rect(), 18, 18)

    # ── Drag per spostare la finestra ───────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def contextMenuEvent(self, event):   # tasto destro → chiudi
        self.close()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = TransparentWindow()
    win.show()
    sys.exit(app.exec())