# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Browse earlier versions of minidlna.conf and restore one."""

from __future__ import annotations

import html
import time
from typing import List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor, QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from . import backups, theme


class BackupDialog(QDialog):
    #: The text of the version the user chose to restore.
    restoreRequested = Signal(str)

    def __init__(self, current_text: str, found: List[backups.Backup], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Restore a previous configuration")
        self.setStyleSheet("QDialog { background: #0C1120; }")
        self.resize(900, 560)
        self._current = current_text
        self._backups = found
        self._chosen = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        intro = QLabel(
            "Pick an earlier version to see how it differs from the file now. "
            "Restoring saves it as the current configuration and restarts minidlna; "
            "the file you are replacing is kept as a backup too."
        )
        intro.setWordWrap(True)
        intro.setObjectName("subtitle")
        layout.addWidget(intro)

        body = QHBoxLayout()
        body.setSpacing(12)

        self.list = QListWidget(self)
        self.list.setMinimumWidth(260)
        self.list.setMaximumWidth(300)
        for backup in found:
            added, removed = backups.change_summary(current_text, backups.read_backup(backup.path))
            stamp = time.strftime("%d %b %Y  %H:%M", time.localtime(backup.time))
            summary = "identical to now" if not (added or removed) else f"+{added}  −{removed} lines"
            item = QListWidgetItem(f"{stamp}\n{summary}")
            item.setData(Qt.UserRole, backup.path)
            self.list.addItem(item)
        self.list.currentRowChanged.connect(self._show)
        body.addWidget(self.list)

        self.view = QTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.view.setLineWrapMode(QTextEdit.NoWrap)
        body.addWidget(self.view, 1)
        layout.addLayout(body, 1)

        footer = QHBoxLayout()
        self.note = QLabel("")
        self.note.setObjectName("subtitle")
        footer.addWidget(self.note, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        self.restore = QPushButton("Restore this version")
        self.restore.setObjectName("primary")
        self.restore.setCursor(QCursor(Qt.PointingHandCursor))
        self.restore.clicked.connect(self._on_restore)
        footer.addWidget(self.restore)
        layout.addLayout(footer)

        self.restore.setEnabled(False)
        if found:
            self.list.setCurrentRow(0)

    def _show(self, row: int) -> None:
        if not 0 <= row < len(self._backups):
            self.restore.setEnabled(False)
            return
        text = backups.read_backup(self._backups[row].path)
        self._chosen = text
        lines = backups.diff_lines(self._current, text)
        changed = any(kind in ("add", "del") for kind, _ in lines)

        if not changed:
            self.view.setHtml(
                f"<p style='color:{theme.TEXT_DIM}'>This version is identical to the "
                "current file.</p>"
            )
            self.note.setText("")
            self.restore.setEnabled(False)
            return

        palette = {
            "add": (theme.GREEN, "+ "),
            "del": (theme.RED, "− "),
            "ctx": (theme.TEXT_DIM, "  "),
            "gap": (theme.TEXT_FAINT, "  "),
        }
        parts = []
        for kind, line in lines:
            colour, mark = palette[kind]
            parts.append(
                f"<div style='color:{colour}; white-space:pre'>{mark}{html.escape(line)}</div>"
            )
        self.view.setHtml("".join(parts))
        added, removed = backups.change_summary(self._current, text)
        self.note.setText(
            f"Restoring adds {added} line{'' if added == 1 else 's'} and removes "
            f"{removed} compared with the file now."
        )
        self.restore.setEnabled(True)

    def _on_restore(self) -> None:
        if self._chosen:
            self.restoreRequested.emit(self._chosen)
            self.accept()
