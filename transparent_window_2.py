"""
test_transparent_window.py
Testa: finestra frameless, no sfondo, trasparenza reale, non resizeable.
Esegui: python test_transparent_window.py
"""

import sys
from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore    import Qt, QPoint
from PyQt6.QtGui     import QPainter, QColor, QPen, QFont

class TransparentOverlay(QWidget):

    def __init__(self):
        super().__init__()

        # --- Nessun bordo OS, nessuno sfondo OS, non resizeable
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint      |
            Qt.WindowType.WindowStaysOnTopHint     |
            Qt.WindowType.Tool                     # non appare nella taskbar
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(1024, 600)               # non resizeable

        # drag manuale (opzionale — per testare su desktop)
        self._drag_pos = QPoint()

    # --- Tutto il rendering è Qt puro — nessun bordo OS coinvolto
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Sfondo completamente trasparente (alpha=0) — si vede ciò che sta sotto
        p.fillRect(self.rect(), QColor(0, 0, 0, 0))

        # Pannello navbar: frosted-glass simulato (nero 70% alpha)
        p.setBrush(QColor(10, 10, 10, 178))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 520, 1024, 80, 16, 16)

        # Pannello overlay laterale: semi-trasparente blu
        p.setBrush(QColor(30, 60, 120, 200))
        p.drawRoundedRect(20, 20, 300, 480, 12, 12)

        # Bordo sottile del pannello
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawRoundedRect(20, 20, 300, 480, 12, 12)

        # Testo dentro il pannello
        p.setPen(QColor(255, 255, 255, 230))
        p.setFont(QFont("Sans", 14))
        p.drawText(40, 60, "Overlay Panel")
        p.setFont(QFont("Sans", 10))
        p.drawText(40, 90,  "alpha=200  → semi-trasparente")
        p.drawText(40, 115, "sfondo: vedi attraverso")
        p.drawText(40, 140, "bordi: Qt puro, non OS")

        # HUD in alto a destra
        p.setBrush(QColor(0, 200, 100, 160))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(820, 20, 180, 48, 24, 24)
        p.setPen(QColor(255, 255, 255, 240))
        p.setFont(QFont("Sans", 11))
        p.drawText(840, 50, "● Connesso")

    # --- Drag per testare su desktop (non serve in produzione fullscreen)
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = TransparentOverlay()
    w.show()
    sys.exit(app.exec())