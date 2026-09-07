"""
media_card_widget.py — Dedicated Media Player Card Widget for Connected Home/Clock Screen.

Renders high-res album art thumbnail with rounded corners, song title, artist, album,
dynamic playing/paused badge, and source indicators directly from Android Auto metadata.
"""

import base64
from typing import Optional, Any
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPixmap, QImage, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .svg_utils import make_svg_icon


class MediaCardWidget(QFrame):
    """
    Glassmorphism Now Playing Media Player Card.
    """

    media_action_requested = pyqtSignal(int)  # 85=play/pause, 87=next, 88=prev

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

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(14, 12, 14, 12)
        self.main_layout.setSpacing(8)

        # Header Bar
        self.header_layout = QHBoxLayout()
        self.header_layout.setSpacing(6)

        self.badge_label = QLabel("NOW PLAYING", self)
        self.badge_label.setStyleSheet("""
            color: #58a6ff;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1px;
        """)
        self.header_layout.addWidget(self.badge_label)
        self.header_layout.addStretch()
        self.main_layout.addLayout(self.header_layout)

        # Content Widget
        self.content_widget = QWidget(self)
        np_layout = QHBoxLayout(self.content_widget)
        np_layout.setContentsMargins(0, 0, 0, 0)
        np_layout.setSpacing(14)

        # Album Art
        self.art_label = QLabel(self.content_widget)
        self.art_label.setFixedSize(90, 90)
        self.art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art_label.setStyleSheet("""
            background: rgba(13, 17, 23, 0.85);
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.08);
        """)
        self._set_default_art()
        np_layout.addWidget(self.art_label)

        # Text Metadata
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self.title_label = QLabel("Not Playing", self.content_widget)
        self.title_label.setStyleSheet("color: #f0f6fc; font-size: 16px; font-weight: 600;")
        self.title_label.setWordWrap(True)
        text_layout.addWidget(self.title_label)

        self.artist_label = QLabel("—", self.content_widget)
        self.artist_label.setStyleSheet("color: #8b949e; font-size: 13px; font-weight: 500;")
        text_layout.addWidget(self.artist_label)

        self.album_label = QLabel("", self.content_widget)
        self.album_label.setStyleSheet("color: #6e7681; font-size: 11px; font-weight: 400;")
        text_layout.addWidget(self.album_label)

        np_layout.addLayout(text_layout)
        np_layout.addStretch()
        self.main_layout.addWidget(self.content_widget)

        # Playback Controls Row (Prev, Play/Pause, Next)
        self.controls_widget = QWidget(self)
        ctrl_layout = QHBoxLayout(self.controls_widget)
        ctrl_layout.setContentsMargins(0, 6, 0, 2)
        ctrl_layout.setSpacing(16)
        ctrl_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        btn_qss = """
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 18px;
                min-width: 36px;
                max-width: 36px;
                min-height: 36px;
                max-height: 36px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.18);
                border-color: rgba(255, 255, 255, 0.28);
            }
            QPushButton:pressed {
                background-color: rgba(88, 166, 255, 0.3);
            }
        """

        self.btn_prev = QPushButton(self.controls_widget)
        self.btn_prev.setIcon(make_svg_icon("skip_previous", color="#f0f6fc", size=18))
        self.btn_prev.setIconSize(QSize(18, 18))
        self.btn_prev.setStyleSheet(btn_qss)
        self.btn_prev.setToolTip("Previous Track")
        self.btn_prev.clicked.connect(lambda: self.media_action_requested.emit(88))
        ctrl_layout.addWidget(self.btn_prev)

        self.btn_playpause = QPushButton(self.controls_widget)
        self.btn_playpause.setIcon(make_svg_icon("play", color="#f0f6fc", size=20))
        self.btn_playpause.setIconSize(QSize(20, 20))
        self.btn_playpause.setStyleSheet(btn_qss)
        self.btn_playpause.setToolTip("Play / Pause")
        self.btn_playpause.clicked.connect(lambda: self.media_action_requested.emit(85))
        ctrl_layout.addWidget(self.btn_playpause)

        self.btn_next = QPushButton(self.controls_widget)
        self.btn_next.setIcon(make_svg_icon("skip_next", color="#f0f6fc", size=18))
        self.btn_next.setIconSize(QSize(18, 18))
        self.btn_next.setStyleSheet(btn_qss)
        self.btn_next.setToolTip("Next Track")
        self.btn_next.clicked.connect(lambda: self.media_action_requested.emit(87))
        ctrl_layout.addWidget(self.btn_next)

        self.main_layout.addWidget(self.controls_widget)

    def update_metadata(
        self,
        title: Optional[str] = None,
        artist: Optional[str] = None,
        album: Optional[str] = None,
        album_art_b64: Optional[str] = None,
        playback_state: Optional[int] = None,
        media_source: Optional[str] = None,
        position_seconds: Optional[int] = None,
    ):
        """Atomically update media info, preserving existing fields when partial updates occur."""
        if title is not None:
            self._current_title = title
        if artist is not None:
            self._current_artist = artist
        if album is not None:
            self._current_album = album
        if album_art_b64 is not None and album_art_b64 != "":
            self._current_album_art_b64 = album_art_b64
        if playback_state is not None:
            self._current_playback_state = playback_state
        if media_source is not None:
            self._current_media_source = media_source
        if position_seconds is not None:
            self._current_position_seconds = position_seconds

        cur_title = getattr(self, "_current_title", "")
        cur_artist = getattr(self, "_current_artist", "")
        cur_album = getattr(self, "_current_album", "")
        cur_art = getattr(self, "_current_album_art_b64", "")
        cur_state = getattr(self, "_current_playback_state", 0)
        cur_source = getattr(self, "_current_media_source", "")
        cur_pos = getattr(self, "_current_position_seconds", 0)

        if not cur_title and not cur_artist:
            self.title_label.setText("No Active Media")
            self.artist_label.setText("—")
            self.album_label.setText("")
            self.badge_label.setText("IDLE")
            self.badge_label.setStyleSheet("color: #8b949e; font-size: 11px; font-weight: 700; letter-spacing: 1.5px;")
            self._set_default_art()
            return

        self.title_label.setText(cur_title or "Unknown Track")
        self.artist_label.setText(cur_artist or "Unknown Artist")

        pos_str = f"{cur_pos // 60}:{cur_pos % 60:02d}" if cur_pos > 0 else ""
        sub_parts = [p for p in (cur_album, pos_str) if p]
        self.album_label.setText(" • ".join(sub_parts))

        source_tag = f" • {cur_source.upper()}" if cur_source else ""
        if cur_state == 2:  # PLAYING
            self.badge_label.setText(f"NOW PLAYING{source_tag}")
            self.badge_label.setStyleSheet("color: #3fb950; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
            self.btn_playpause.setIcon(make_svg_icon("pause", color="#f0f6fc", size=20))
            self.btn_playpause.setToolTip("Pause")
        elif cur_state == 3:  # PAUSED
            self.badge_label.setText(f"PAUSED{source_tag}")
            self.badge_label.setStyleSheet("color: #d29922; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
            self.btn_playpause.setIcon(make_svg_icon("play", color="#f0f6fc", size=20))
            self.btn_playpause.setToolTip("Play")
        else:
            self.badge_label.setText(f"MEDIA{source_tag}")
            self.badge_label.setStyleSheet("color: #58a6ff; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
            self.btn_playpause.setIcon(make_svg_icon("play", color="#f0f6fc", size=20))
            self.btn_playpause.setToolTip("Play")

        # Render Album Art
        if cur_art and "base64," in cur_art:
            try:
                raw_b64 = cur_art.split("base64,")[1]
                img_data = base64.b64decode(raw_b64)
                img = QImage.fromData(img_data)
                if not img.isNull():
                    pix = QPixmap.fromImage(img).scaled(
                        90, 90,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    self._set_rounded_pixmap(pix)
                    return
            except Exception:
                pass

        self._set_default_art()

    def _set_rounded_pixmap(self, src_pix: QPixmap):
        size = 90
        target = QPixmap(size, size)
        target.fill(Qt.GlobalColor.transparent)

        painter = QPainter(target)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, size, size, 12, 12)
        painter.setClipPath(path)

        x = (size - src_pix.width()) // 2
        y = (size - src_pix.height()) // 2
        painter.drawPixmap(x, y, src_pix)
        painter.end()

        self.art_label.setPixmap(target)

    def _set_default_art(self):
        size = 90
        target = QPixmap(size, size)
        target.fill(Qt.GlobalColor.transparent)

        painter = QPainter(target)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(30, 41, 59, 180))
        painter.drawRoundedRect(0, 0, size, size, 12, 12)

        painter.setPen(QColor(88, 166, 255))
        font = QFont()
        font.setPointSize(24)
        painter.setFont(font)
        painter.drawText(0, 0, size, size, Qt.AlignmentFlag.AlignCenter, "🎵")
        painter.end()

        self.art_label.setPixmap(target)
