"""
media_card_widget.py — Dedicated Media Player Card Widget for Connected Home/Clock Screen.

Renders high-res album art thumbnail with rounded corners, song title, artist, album,
and dynamic playing/paused badge.
"""

import base64
from typing import Optional
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor, QFont, QPixmap, QImage, QPainter, QPainterPath
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class MediaCardWidget(QFrame):
    """
    Glassmorphism Media Player Card.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("media-card-widget")
        self.setStyleSheet("""
            QFrame#media-card-widget {
                background: rgba(22, 27, 34, 0.75);
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 16px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)

        # 1. Album Art Thumbnail View
        self.art_label = QLabel(self)
        self.art_label.setFixedSize(110, 110)
        self.art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art_label.setStyleSheet("""
            background: rgba(13, 17, 23, 0.85);
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.08);
        """)
        self._set_default_art()
        layout.addWidget(self.art_label)

        # 2. Track Metadata Information
        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)
        text_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # Source / Status Badge
        self.badge_label = QLabel("NOW PLAYING", self)
        self.badge_label.setStyleSheet("""
            color: #58a6ff;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 1.5px;
        """)
        text_layout.addWidget(self.badge_label)

        # Song Title
        self.title_label = QLabel("Not Playing", self)
        self.title_label.setStyleSheet("""
            color: #f0f6fc;
            font-size: 18px;
            font-weight: 600;
        """)
        self.title_label.setWordWrap(True)
        text_layout.addWidget(self.title_label)

        # Artist
        self.artist_label = QLabel("—", self)
        self.artist_label.setStyleSheet("""
            color: #8b949e;
            font-size: 14px;
            font-weight: 500;
        """)
        text_layout.addWidget(self.artist_label)

        # Album
        self.album_label = QLabel("", self)
        self.album_label.setStyleSheet("""
            color: #6e7681;
            font-size: 12px;
            font-weight: 400;
        """)
        text_layout.addWidget(self.album_label)

        layout.addLayout(text_layout)
        layout.addStretch()

    def update_metadata(self, title: str, artist: str, album: str = "", album_art_b64: str = "", playback_state: int = 0):
        """Update media info with song details and optional base64 album cover."""
        if not title and not artist:
            self.title_label.setText("No Active Media")
            self.artist_label.setText("—")
            self.album_label.setText("")
            self.badge_label.setText("IDLE")
            self._set_default_art()
            return

        self.title_label.setText(title or "Unknown Track")
        self.artist_label.setText(artist or "Unknown Artist")
        self.album_label.setText(album or "")

        # State text
        if playback_state == 2:  # PLAYING
            self.badge_label.setText("NOW PLAYING")
            self.badge_label.setStyleSheet("color: #3fb950; font-size: 11px; font-weight: 700; letter-spacing: 1.5px;")
        elif playback_state == 3:  # PAUSED
            self.badge_label.setText("PAUSED")
            self.badge_label.setStyleSheet("color: #d29922; font-size: 11px; font-weight: 700; letter-spacing: 1.5px;")
        else:
            self.badge_label.setText("MEDIA")
            self.badge_label.setStyleSheet("color: #58a6ff; font-size: 11px; font-weight: 700; letter-spacing: 1.5px;")

        # Render Album Art
        if album_art_b64 and "base64," in album_art_b64:
            try:
                raw_b64 = album_art_b64.split("base64,")[1]
                img_data = base64.b64decode(raw_b64)
                img = QImage.fromData(img_data)
                if not img.isNull():
                    pix = QPixmap.fromImage(img).scaled(
                        110, 110,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    self._set_rounded_pixmap(pix)
                    return
            except Exception:
                pass

        self._set_default_art()

    def _set_rounded_pixmap(self, src_pix: QPixmap):
        """Clip pixmap to rounded rectangle for premium presentation."""
        size = 110
        target = QPixmap(size, size)
        target.fill(Qt.GlobalColor.transparent)

        painter = QPainter(target)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, size, size, 12, 12)
        painter.setClipPath(path)

        # Center crop
        x = (size - src_pix.width()) // 2
        y = (size - src_pix.height()) // 2
        painter.drawPixmap(x, y, src_pix)
        painter.end()

        self.art_label.setPixmap(target)

    def _set_default_art(self):
        """Draw aesthetic vinyl/music icon fallback."""
        size = 110
        target = QPixmap(size, size)
        target.fill(Qt.GlobalColor.transparent)

        painter = QPainter(target)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background rounded rect
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(30, 41, 59, 180))
        painter.drawRoundedRect(0, 0, size, size, 12, 12)

        # Music note emoji / text
        painter.setPen(QColor(88, 166, 255))
        font = QFont()
        font.setPointSize(28)
        painter.setFont(font)
        painter.drawText(0, 0, size, size, Qt.AlignmentFlag.AlignCenter, "🎵")
        painter.end()

        self.art_label.setPixmap(target)
