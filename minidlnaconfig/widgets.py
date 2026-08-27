# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Custom widgets: glass panels, media-type chips, folder rows and toasts."""

from __future__ import annotations

import os
from typing import List, Optional

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .models import MediaFolder, MediaKind


def _rgba(color: str, alpha: float) -> QColor:
    qcolor = QColor(color)
    qcolor.setAlphaF(alpha)
    return qcolor


class GlassPanel(QFrame):
    """A rounded, translucent panel that reads as frosted glass."""

    def __init__(self, parent=None, radius: int = 16, fill: float = 0.05,
                 stroke: float = 0.11, base: Optional[QColor] = None):
        super().__init__(parent)
        self._radius = radius
        self._fill = fill
        self._stroke = stroke
        # Panels that carry text over the moving backdrop need something
        # opaque underneath, otherwise the aurora shows through the words.
        self._base = base
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        path = QPainterPath()
        path.addRoundedRect(rect, self._radius, self._radius)

        if self._base is not None:
            painter.fillPath(path, self._base)
        painter.fillPath(path, _rgba("#FFFFFF", self._fill))
        painter.setPen(QPen(_rgba("#FFFFFF", self._stroke), 1))
        painter.drawPath(path)
        painter.end()


class KindChip(QPushButton):
    """A checkable pill for one media type, with an animated fill."""

    def __init__(self, letter: str, label: str, parent=None):
        super().__init__(label, parent)
        self.letter = letter
        self.color = QColor(theme.KIND_COLORS[letter])
        self._fill = 0.0

        self.setCheckable(True)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setFlat(True)
        self.setFixedHeight(27)
        font = QFont(self.font())
        font.setPointSizeF(9.5)
        font.setWeight(QFont.DemiBold)
        self.setFont(font)
        self.setMinimumWidth(self.fontMetrics().horizontalAdvance(label) + 26)
        self.setToolTip(f"Index {label.lower()} in this folder")

        self._animation = QPropertyAnimation(self, b"fill", self)
        self._animation.setDuration(180)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._fill)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def get_fill(self) -> float:
        return self._fill

    def set_fill(self, value: float) -> None:
        self._fill = value
        self.update()

    fill = Property(float, get_fill, set_fill)

    def setChecked(self, checked: bool) -> None:  # noqa: N802 - Qt naming
        super().setChecked(checked)
        self._fill = 1.0 if checked else 0.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        hover = 0.06 if self.underMouse() else 0.0
        painter.fillPath(path, _rgba(self.color.name(), 0.10 * self._fill + hover))
        painter.setPen(QPen(_rgba(self.color.name(), 0.25 + 0.55 * self._fill), 1))
        painter.drawPath(path)

        # A filled dot reinforces the on/off state for anyone who cannot rely
        # on the colour change alone.
        dot = QRectF(rect.left() + 9, rect.center().y() - 3, 6, 6)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_rgba(self.color.name(), 0.30 + 0.70 * self._fill))
        painter.drawEllipse(dot)

        text_color = QColor(theme.TEXT)
        text_color.setAlphaF(0.45 + 0.55 * self._fill)
        painter.setPen(text_color)
        painter.drawText(rect.adjusted(21, 0, -8, 0), Qt.AlignCenter, self.text())
        painter.end()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


class KindSelector(QWidget):
    """The three media-type chips, kept to at least one selection."""

    changed = Signal(object)

    def __init__(self, kinds: MediaKind, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._chips: List[KindChip] = []
        for letter, label, member in (
            ("A", "Audio", MediaKind.AUDIO),
            ("P", "Pictures", MediaKind.PICTURES),
            ("V", "Video", MediaKind.VIDEO),
        ):
            chip = KindChip(letter, label, self)
            chip.member = member
            chip.setChecked(bool(kinds & member))
            chip.clicked.connect(self._on_clicked)
            layout.addWidget(chip)
            self._chips.append(chip)

    def _on_clicked(self) -> None:
        chip = self.sender()
        if not any(other.isChecked() for other in self._chips):
            # Refuse to leave a folder indexing nothing at all.
            chip.setChecked(True)
            return
        self.changed.emit(self.kinds())

    def kinds(self) -> MediaKind:
        result = None
        for chip in self._chips:
            if chip.isChecked():
                result = chip.member if result is None else result | chip.member
        return result or MediaKind.ALL

    def set_kinds(self, kinds: MediaKind) -> None:
        for chip in self._chips:
            chip.setChecked(bool(kinds & chip.member))


class StatusPill(QWidget):
    """Service state indicator with a slow pulse while the server is up."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = "checking…"
        self._ok = False
        self._phase = 0.0
        self.setFixedHeight(30)
        self.setMinimumWidth(140)

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self) -> None:
        self._phase += 0.04
        if self._ok:
            self.update()

    def set_state(self, ok: bool, text: str) -> None:
        self._ok, self._text = ok, text
        metrics = self.fontMetrics()
        self.setMinimumWidth(metrics.horizontalAdvance(text) + 46)
        self.update()

    def paintEvent(self, event):
        import math

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2

        accent = theme.GREEN if self._ok else theme.TEXT_FAINT
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.fillPath(path, _rgba(accent if self._ok else "#FFFFFF", 0.07))
        painter.setPen(QPen(_rgba(accent if self._ok else "#FFFFFF", 0.18), 1))
        painter.drawPath(path)

        centre = QRectF(rect.left() + 13, rect.center().y() - 3.5, 7, 7)
        painter.setPen(Qt.NoPen)
        if self._ok:
            pulse = 0.5 + 0.5 * math.sin(self._phase)
            halo = QRectF(centre).adjusted(-4 * pulse, -4 * pulse, 4 * pulse, 4 * pulse)
            painter.setBrush(_rgba(theme.GREEN, 0.30 * (1 - pulse)))
            painter.drawEllipse(halo)
            painter.setBrush(QColor(theme.GREEN))
        else:
            painter.setBrush(_rgba("#FFFFFF", 0.30))
        painter.drawEllipse(centre)

        color = QColor(theme.TEXT)
        color.setAlphaF(0.80 if self._ok else 0.50)
        painter.setPen(color)
        painter.drawText(rect.adjusted(28, 0, -12, 0), Qt.AlignVCenter | Qt.AlignLeft, self._text)
        painter.end()


class WarningBadge(QLabel):
    """Small inline badge explaining why a folder will not work."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVisible(False)
        font = QFont(self.font())
        font.setPointSizeF(9.0)
        font.setWeight(QFont.DemiBold)
        self.setFont(font)

    def show_warning(self, text: str, tooltip: str, color: str) -> None:
        self.setText(f"  {text}  ")
        self.setToolTip(tooltip)
        self.setStyleSheet(
            f"color: {color};"
            f"background: rgba({QColor(color).red()},{QColor(color).green()},"
            f"{QColor(color).blue()},0.13);"
            "border-radius: 7px; padding: 2px 4px;"
        )
        self.setVisible(True)

    def clear_warning(self) -> None:
        self.setVisible(False)


class FolderRow(GlassPanel):
    """One media folder: name, editable path, media-type chips, actions."""

    changed = Signal()
    removeRequested = Signal(object)
    browseRequested = Signal(object)

    def __init__(self, folder: MediaFolder, parent=None):
        super().__init__(parent, radius=14, fill=0.04, stroke=0.08)
        self.folder = folder
        self._hover = 0.0
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 10, 12, 10)
        outer.setSpacing(5)

        # --- top line: name, warning badge, action buttons
        top = QHBoxLayout()
        top.setSpacing(8)

        self.name_label = QLabel(folder.name)
        self.name_label.setObjectName("folderName")
        top.addWidget(self.name_label)

        self.badge = WarningBadge(self)
        top.addWidget(self.badge)
        top.addStretch(1)

        self.open_button = self._icon_button("↗", "Open this folder in your file manager")
        self.open_button.clicked.connect(self._open_in_file_manager)
        top.addWidget(self.open_button)

        self.browse_button = self._icon_button("⋯", "Choose a different folder")
        self.browse_button.clicked.connect(lambda: self.browseRequested.emit(self))
        top.addWidget(self.browse_button)

        self.remove_button = self._icon_button("✕", "Remove this folder")
        self.remove_button.setObjectName("iconDanger")
        self.remove_button.clicked.connect(lambda: self.removeRequested.emit(self))
        top.addWidget(self.remove_button)

        outer.addLayout(top)

        # --- bottom line: inline-editable path and the media-type chips
        bottom = QHBoxLayout()
        bottom.setSpacing(10)

        self.path_edit = QLineEdit(folder.path)
        self.path_edit.setObjectName("pathEdit")
        self.path_edit.setToolTip("Click to edit the path directly")
        self.path_edit.editingFinished.connect(self._on_path_edited)
        bottom.addWidget(self.path_edit, 1)

        self.selector = KindSelector(folder.kinds, self)
        self.selector.changed.connect(self._on_kinds_changed)
        bottom.addWidget(self.selector, 0)

        outer.addLayout(bottom)

        self._glow_animation = QPropertyAnimation(self, b"hover", self)
        self._glow_animation.setDuration(150)
        self._glow_animation.setEasingCurve(QEasingCurve.OutCubic)

    def _icon_button(self, glyph: str, tooltip: str) -> QPushButton:
        button = QPushButton(glyph, self)
        button.setObjectName("iconGhost")
        button.setToolTip(tooltip)
        button.setCursor(QCursor(Qt.PointingHandCursor))
        button.setFixedSize(QSize(30, 30))
        return button

    # ------------------------------------------------------------ behaviour

    def _on_path_edited(self) -> None:
        text = self.path_edit.text().strip()
        if text and text != self.folder.path:
            self.folder.path = os.path.expanduser(text)
            self.path_edit.setText(self.folder.path)
            self.name_label.setText(self.folder.name)
            self.changed.emit()

    def _on_kinds_changed(self, kinds: MediaKind) -> None:
        self.folder.kinds = kinds
        self.changed.emit()

    def _open_in_file_manager(self) -> None:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl.fromLocalFile(self.folder.path))

    def set_path(self, path: str) -> None:
        self.folder.path = path
        self.path_edit.setText(path)
        self.name_label.setText(self.folder.name)
        self.changed.emit()

    def apply_health(self, exists: bool, readable: Optional[bool]) -> None:
        """Show why this folder will not be served, if that is the case."""
        self.open_button.setEnabled(exists)
        if not exists:
            self.badge.show_warning(
                "not found",
                "This directory does not exist right now.\n"
                "If it lives on a removable or network drive, it may simply "
                "not be mounted - minidlna will skip it until it is.",
                theme.AMBER,
            )
        elif readable is False:
            self.badge.show_warning(
                "unreadable",
                "The account minidlna runs as cannot read this directory, so "
                "it will appear empty to your DLNA clients.\n"
                "Fix the directory permissions, or add that account to a group "
                "that can read it.",
                theme.RED,
            )
        else:
            self.badge.clear_warning()

    # ---------------------------------------------------------- hover paint

    def get_hover(self) -> float:
        return self._hover

    def set_hover(self, value: float) -> None:
        self._hover = value
        self.update()

    hover = Property(float, get_hover, set_hover)

    def enterEvent(self, event):
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def _animate_hover(self, target: float) -> None:
        self._glow_animation.stop()
        self._glow_animation.setStartValue(self._hover)
        self._glow_animation.setEndValue(target)
        self._glow_animation.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)

        painter.fillPath(path, _rgba("#FFFFFF", 0.04 + 0.035 * self._hover))
        painter.setPen(QPen(_rgba("#FFFFFF", 0.08 + 0.10 * self._hover), 1))
        painter.drawPath(path)

        # A colour bar on the left edge, tinted by the selected media types.
        letters = self.folder.kinds.letters or "APV"
        bar = QRectF(rect.left() + 1, rect.top() + 12, 3, rect.height() - 24)
        segment_height = bar.height() / len(letters)
        painter.setPen(Qt.NoPen)
        for index, letter in enumerate(letters):
            segment = QRectF(
                bar.left(), bar.top() + index * segment_height, bar.width(), segment_height
            )
            segment_path = QPainterPath()
            segment_path.addRoundedRect(segment, 1.5, 1.5)
            painter.fillPath(segment_path, _rgba(theme.KIND_COLORS[letter], 0.55 + 0.45 * self._hover))
        painter.end()


class Toast(GlassPanel):
    """A transient message with an optional single action (e.g. Undo)."""

    def __init__(self, message: str, kind: str = "info", action_text: str = "",
                 on_action=None, timeout: int = 4200, parent=None):
        super().__init__(parent, radius=12, fill=0.06, stroke=0.16,
                         base=QColor(17, 22, 40, 243))
        self._accent = {
            "info": theme.INDIGO,
            "success": theme.GREEN,
            "error": theme.RED,
            "warning": theme.AMBER,
        }.get(kind, theme.INDIGO)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 11, 12, 11)
        layout.setSpacing(11)

        glyph = {"info": "•", "success": "✓", "error": "!", "warning": "!"}.get(kind, "•")
        icon = QLabel(glyph)
        icon.setStyleSheet(f"color: {self._accent}; font-size: 15px; font-weight: 700;")
        icon.setFixedWidth(14)
        layout.addWidget(icon)

        label = QLabel(message)
        label.setWordWrap(True)
        # A word-wrapped label only reports a usable height once its width is
        # pinned, so fix the width and let the layout derive the rest.
        label.setFixedWidth(300)
        layout.addWidget(label, 1)

        if action_text and on_action:
            button = QPushButton(action_text)
            button.setCursor(QCursor(Qt.PointingHandCursor))
            button.setStyleSheet(
                f"background: transparent; border: none; color: {self._accent};"
                "font-weight: 600; padding: 4px 8px;"
            )

            def triggered():
                on_action()
                self.dismiss()

            button.clicked.connect(triggered)
            layout.addWidget(button)

        self.adjustSize()
        self.setFixedSize(self.sizeHint())

        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._effect.setOpacity(0.0)

        if timeout:
            QTimer.singleShot(timeout, self.dismiss)

    def appear(self, position: QPoint) -> None:
        self.move(position + QPoint(0, 14))
        self.show()
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(260)
        self._slide.setEasingCurve(QEasingCurve.OutCubic)
        self._slide.setEndValue(position)
        self._slide.start()

        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(220)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def dismiss(self) -> None:
        if not self.isVisible():
            return
        self._fade_out = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade_out.setDuration(200)
        self._fade_out.setEndValue(0.0)
        self._fade_out.finished.connect(self.deleteLater)
        self._fade_out.start()


class ToastHost(QWidget):
    """Stacks toasts in the bottom-right corner of the window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._toasts: List[Toast] = []

    def post(self, message: str, kind: str = "info", action_text: str = "",
             on_action=None, timeout: int = 4200) -> None:
        toast = Toast(message, kind, action_text, on_action, timeout, self.parentWidget())
        toast.destroyed.connect(lambda: self._forget(toast))
        self._toasts.append(toast)
        self._relayout(new=toast)

    def _forget(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        QTimer.singleShot(0, self._relayout)

    def _relayout(self, new: Optional[Toast] = None) -> None:
        parent = self.parentWidget()
        if not parent:
            return
        margin = 22
        # Sit above the footer so a toast never covers the Apply button.
        y = parent.height() - margin - 44
        for toast in reversed(self._toasts):
            try:
                height = toast.height()
            except RuntimeError:      # already deleted
                continue
            y -= height + 10
            position = QPoint(parent.width() - toast.width() - margin, y)
            if toast is new:
                toast.appear(position)
                toast.raise_()
            else:
                toast.move(position)
