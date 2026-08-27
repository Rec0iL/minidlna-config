# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""The animated aurora backdrop.

Large soft colour fields drift behind the UI.  They are rendered into a small
off-screen image and scaled up with smooth interpolation, which costs almost
nothing per frame and gives the blur for free - painting full-size radial
gradients every frame would be far more expensive for the same result.
"""

from __future__ import annotations

import math
import os
import random

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from . import theme

#: The aurora is rendered at 1/DOWNSCALE resolution before being scaled up.
DOWNSCALE = 10
FRAME_MS = 16  # ~60fps


class _Blob:
    """One drifting field of colour."""

    __slots__ = ("color", "radius", "cx", "cy", "ax", "ay", "sx", "sy", "px", "py", "pulse")

    def __init__(self, color: str, radius: float, cx: float, cy: float,
                 ax: float, ay: float, sx: float, sy: float, px: float, py: float):
        self.color = QColor(color)
        self.radius = radius   # as a fraction of the widget diagonal
        self.cx, self.cy = cx, cy   # anchor, in 0..1 widget coordinates
        self.ax, self.ay = ax, ay   # drift amplitude
        self.sx, self.sy = sx, sy   # drift speed
        self.px, self.py = px, py   # phase offsets
        self.pulse = random.uniform(0.6, 1.4)

    def position(self, t: float) -> QPointF:
        return QPointF(
            self.cx + self.ax * math.sin(t * self.sx + self.px),
            self.cy + self.ay * math.cos(t * self.sy + self.py),
        )


class AuroraBackground(QWidget):
    """Full-window animated backdrop.

    Set ``MINIDLNA_GUI_NO_ANIM=1`` to freeze it on a single static frame.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.lower()

        self._t = 0.0
        self._buffer: QImage | None = None
        self._grain: QImage | None = None
        self._stars: list[tuple[float, float, float, float]] = []

        self._blobs = [
            _Blob(theme.VIOLET, 0.30, 0.16, 0.12, 0.10, 0.07, 0.21, 0.17, 0.0, 1.1),
            _Blob(theme.INDIGO, 0.34, 0.86, 0.20, 0.09, 0.08, 0.16, 0.23, 2.1, 0.4),
            _Blob(theme.CYAN,   0.24, 0.78, 0.90, 0.12, 0.06, 0.13, 0.27, 4.2, 2.6),
            _Blob(theme.PINK,   0.21, 0.20, 0.93, 0.10, 0.07, 0.24, 0.15, 1.4, 3.3),
            _Blob("#3B1D8F",    0.40, 0.50, 0.55, 0.06, 0.05, 0.09, 0.11, 3.0, 0.8),
        ]

        self._radius = 0.0
        self._animated = os.environ.get("MINIDLNA_GUI_NO_ANIM", "") != "1"
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)
        if self._animated:
            self._timer.start()

    def set_radius(self, radius: float) -> None:
        """Round the backdrop's own corners.

        The frameless window is shaped by painting rather than by setMask(),
        which would give hard aliased edges.
        """
        self._radius = radius
        self.update()

    # ------------------------------------------------------------- lifecycle

    def _tick(self) -> None:
        self._t += FRAME_MS / 1000.0
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if self._animated and not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event):
        # No point burning CPU on a window nobody can see.
        self._timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._buffer = None
        self._seed_stars()

    def _seed_stars(self) -> None:
        rng = random.Random(7)
        count = max(28, min(90, self.width() * self.height() // 12000))
        self._stars = [
            (rng.random(), rng.random(), rng.uniform(0.6, 1.7), rng.uniform(0, math.tau))
            for _ in range(count)
        ]

    def _grain_tile(self) -> QImage:
        """A small tiled noise texture; keeps the gradients from banding."""
        if self._grain is None:
            size = 128
            image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            rng = random.Random(11)
            for y in range(size):
                for x in range(size):
                    value = rng.randint(0, 255)
                    image.setPixelColor(x, y, QColor(value, value, value, 8))
            self._grain = image
        return self._grain

    # ---------------------------------------------------------------- paint

    def paintEvent(self, event):
        width, height = max(self.width(), 1), max(self.height(), 1)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        shape = QPainterPath()
        if self._radius > 0:
            shape.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
            painter.setClipPath(shape)

        # Base wash, slightly lighter at the top.
        base = QLinearGradient(0, 0, 0, height)
        base.setColorAt(0.0, QColor(theme.BG_BASE))
        base.setColorAt(1.0, QColor(theme.BG_DEEP))
        painter.fillRect(self.rect(), base)

        painter.drawImage(self.rect(), self._render_aurora(width, height))
        self._paint_stars(painter, width, height)

        # Vignette to pull focus into the middle of the window.
        vignette = QRadialGradient(width * 0.5, height * 0.45, max(width, height) * 0.78)
        vignette.setColorAt(0.0, QColor(0, 0, 0, 0))
        vignette.setColorAt(0.62, QColor(0, 0, 0, 74))
        vignette.setColorAt(1.0, QColor(0, 0, 0, 190))
        painter.fillRect(self.rect(), QBrush(vignette))

        painter.setOpacity(0.55)
        painter.drawTiledPixmap(self.rect(), self._grain_pixmap())
        painter.setOpacity(1.0)

        # A hairline edge keeps the window from bleeding into a dark desktop.
        if self._radius > 0:
            painter.setClipping(False)
            painter.setPen(QPen(QColor(255, 255, 255, 34), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                self._radius, self._radius,
            )
        painter.end()

    def _grain_pixmap(self):
        from PySide6.QtGui import QPixmap
        if not hasattr(self, "_grain_pm") or self._grain_pm is None:
            self._grain_pm = QPixmap.fromImage(self._grain_tile())
        return self._grain_pm

    def _render_aurora(self, width: int, height: int) -> QImage:
        """Draw the drifting colour fields into a small buffer."""
        small_w = max(2, width // DOWNSCALE)
        small_h = max(2, height // DOWNSCALE)

        if self._buffer is None or self._buffer.width() != small_w or self._buffer.height() != small_h:
            self._buffer = QImage(small_w, small_h, QImage.Format_ARGB32_Premultiplied)

        self._buffer.fill(Qt.transparent)
        painter = QPainter(self._buffer)
        # Additive blending so overlapping fields bloom instead of occluding.
        painter.setCompositionMode(QPainter.CompositionMode_Plus)

        diagonal = math.hypot(small_w, small_h)
        for blob in self._blobs:
            position = blob.position(self._t)
            centre = QPointF(position.x() * small_w, position.y() * small_h)
            radius = blob.radius * diagonal
            # Gentle breathing so the field never looks frozen.
            radius *= 1.0 + 0.06 * math.sin(self._t * 0.35 * blob.pulse + blob.px)

            gradient = QRadialGradient(centre, radius)
            core = QColor(blob.color)
            core.setAlpha(74)
            mid = QColor(blob.color)
            mid.setAlpha(20)
            edge = QColor(blob.color)
            edge.setAlpha(0)
            gradient.setColorAt(0.0, core)
            gradient.setColorAt(0.45, mid)
            gradient.setColorAt(1.0, edge)

            painter.setBrush(QBrush(gradient))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(centre, radius, radius)
        painter.end()
        return self._buffer

    def _paint_stars(self, painter: QPainter, width: int, height: int) -> None:
        if not self._stars:
            self._seed_stars()
        painter.setPen(Qt.NoPen)
        for x, y, size, phase in self._stars:
            twinkle = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(self._t * 0.9 + phase))
            color = QColor(255, 255, 255, int(70 * twinkle))
            painter.setBrush(color)
            painter.drawEllipse(QRectF(x * width, y * height, size, size))
