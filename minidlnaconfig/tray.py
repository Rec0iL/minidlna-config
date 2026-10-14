# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""A system tray icon: status, quick actions and desktop notifications."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


class TrayController(QObject):
    showRequested = Signal()
    restartRequested = Signal()
    rebuildRequested = Signal()
    openWebRequested = Signal()
    quitRequested = Signal()

    def __init__(self, icon: QIcon, parent=None):
        super().__init__(parent)
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("MiniDLNA")

        menu = QMenu()
        self._status = menu.addAction("MiniDLNA")
        self._status.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Show window", self.showRequested.emit)
        menu.addSeparator()
        menu.addAction("Restart server", self.restartRequested.emit)
        menu.addAction("Rebuild media library", self.rebuildRequested.emit)
        menu.addAction("Open web interface", self.openWebRequested.emit)
        menu.addSeparator()
        menu.addAction("Quit", self.quitRequested.emit)
        self._menu = menu          # keep a reference: the tray does not own it
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)

    @staticmethod
    def is_available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def is_visible(self) -> bool:
        return self._tray.isVisible()

    def set_visible(self, visible: bool) -> None:
        if visible and not self.is_available():
            return
        self._tray.setVisible(visible)

    def set_status(self, text: str) -> None:
        self._status.setText(text)
        self._tray.setToolTip(f"MiniDLNA — {text}")

    def notify(self, title: str, message: str) -> None:
        if self._tray.isVisible():
            self._tray.showMessage(title, message, QSystemTrayIcon.Information, 6000)

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.showRequested.emit()
