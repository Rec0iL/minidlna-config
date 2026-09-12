# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""The Library view: what minidlna has actually indexed."""

from __future__ import annotations

import os
import time
from typing import List

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import library, theme
from .widgets import GlassPanel

#: Accent colour per media kind, matching the folder chips.
KIND_COLORS = {"video": theme.VIOLET, "audio": theme.AMBER, "image": theme.CYAN}
KIND_LABELS = {"video": "Video", "audio": "Audio", "image": "Photos"}

#: Rows fetched at once; a bigger library is narrowed with the search box.
PAGE_SIZE = 500


class StatCard(GlassPanel):
    """One headline number: a media kind, its file count and total size."""

    def __init__(self, kind: str, parent=None):
        super().__init__(parent, radius=14, fill=0.045, stroke=0.09)
        self.kind = kind
        self.accent = QColor(KIND_COLORS.get(kind, theme.VIOLET))
        self._share = 0.0
        self.setMinimumHeight(92)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 14)
        layout.setSpacing(2)

        self.title = QLabel(KIND_LABELS.get(kind, kind.title()))
        self.title.setStyleSheet(
            f"color: {KIND_COLORS.get(kind, theme.VIOLET)};"
            "font-size: 11px; font-weight: 700; letter-spacing: 1.2px;"
        )
        layout.addWidget(self.title)

        self.count = QLabel("—")
        font = QFont(self.count.font())
        font.setPointSizeF(17)
        font.setWeight(QFont.DemiBold)
        self.count.setFont(font)
        layout.addWidget(self.count)

        self.size = QLabel("")
        self.size.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")
        layout.addWidget(self.size)

        layout.addStretch(1)

    def set_values(self, count: int, size: int, share: float) -> None:
        self._share = share
        self.count.setText(f"{count:,}")
        self.size.setText(library.format_size(size) if count else "nothing indexed")
        dim = 1.0 if count else 0.40
        self.count.setStyleSheet(f"color: rgba(232,238,255,{dim});")
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        # A share-of-library bar along the bottom edge of the card.
        if self._share <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        width = (self.width() - 32) * min(1.0, self._share)
        path = QPainterPath()
        path.addRoundedRect(16, self.height() - 9, max(width, 3), 3, 1.5, 1.5)
        color = QColor(self.accent)
        color.setAlphaF(0.85)
        painter.fillPath(path, color)
        painter.end()


class LibraryPage(QWidget):
    """Summary cards plus a searchable list of everything minidlna indexed."""

    rebuildRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._db_path = ""
        self._folders: List[str] = []
        self._stats = library.LibraryStats()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        # --- headline cards
        cards = QHBoxLayout()
        cards.setSpacing(10)
        self.cards = {}
        for kind in library.KINDS:
            card = StatCard(kind, self)
            self.cards[kind] = card
            cards.addWidget(card)
        outer.addLayout(cards)

        # --- search and ordering
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search the library…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search_typed)
        controls.addWidget(self.search, 1)

        self.kind_filter = QComboBox(self)
        self.kind_filter.addItem("All types", "")
        for kind in library.KINDS:
            self.kind_filter.addItem(KIND_LABELS[kind], kind)
        self.kind_filter.currentIndexChanged.connect(self.reload_items)
        controls.addWidget(self.kind_filter)

        self.order = QComboBox(self)
        self.order.addItem("Newest first", "recent")
        self.order.addItem("Name", "name")
        self.order.addItem("Largest first", "size")
        self.order.currentIndexChanged.connect(self.reload_items)
        controls.addWidget(self.order)

        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.refresh_button.setToolTip("Re-read minidlna's media database")
        self.refresh_button.clicked.connect(self.refresh)
        controls.addWidget(self.refresh_button)

        outer.addLayout(controls)

        # --- the files themselves
        self.table = QTreeWidget(self)
        self.table.setObjectName("libraryTable")
        self.table.setColumnCount(5)
        self.table.setHeaderLabels(["Name", "Length", "Resolution", "Size", "Folder"])
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSortingEnabled(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemActivated.connect(self._open_containing_folder)

        header = self.table.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Interactive)
        header.resizeSection(4, 240)
        outer.addWidget(self.table, 1)

        # --- status line / empty state
        self.status = QLabel("")
        self.status.setObjectName("subtitle")
        self.status.setWordWrap(True)

        self.rebuild_button = QPushButton("Rebuild library", self)
        self.rebuild_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.rebuild_button.setToolTip(
            "Clear minidlna's database and rescan every folder from scratch"
        )
        self.rebuild_button.clicked.connect(self.rebuildRequested.emit)
        self.rebuild_button.setVisible(False)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addWidget(self.status, 1)
        footer.addWidget(self.rebuild_button)
        outer.addLayout(footer)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(self.reload_items)

    # ------------------------------------------------------------------ data

    def set_source(self, db_path: str, folders: List[str]) -> None:
        self._db_path = db_path
        self._folders = folders

    def refresh(self) -> None:
        """Re-read the database and rebuild the whole view."""
        self._stats = library.read_stats(self._db_path, self._folders)
        total = max(1, self._stats.files)
        for kind, card in self.cards.items():
            count, size = self._stats.kinds.get(kind, (0, 0))
            card.set_values(count, size, count / total)
        self.reload_items()

    def _on_search_typed(self) -> None:
        self._debounce.start()

    def reload_items(self) -> None:
        stats = self._stats
        usable = stats.available and stats.files > 0

        self.table.setVisible(usable)
        self.rebuild_button.setVisible(not usable)
        for widget in (self.search, self.kind_filter, self.order):
            widget.setEnabled(usable)

        if not stats.available:
            reason = stats.error or "unavailable"
            if "no media database" in reason:
                self.status.setText(
                    "minidlna has not built a media library yet. It does this on its "
                    "first scan; rebuilding forces one now."
                )
            else:
                self.status.setText(f"Could not read {stats.db_path}: {reason}")
            return

        if stats.files == 0:
            self.status.setText(
                "The database exists but holds no media. Check that your folders "
                "contain files minidlna recognises, and that it can read them."
            )
            return

        items, total = library.search_items(
            self._db_path,
            query=self.search.text().strip(),
            kind=self.kind_filter.currentData() or "",
            order=self.order.currentData() or "recent",
            limit=PAGE_SIZE,
        )

        self.table.setUpdatesEnabled(False)
        self.table.clear()
        rows = []
        for item in items:
            row = QTreeWidgetItem([
                item.title,
                library.format_duration(item.duration),
                item.resolution,
                library.format_size(item.size),
                self._folder_label(item.folder),
            ])
            row.setData(0, Qt.UserRole, item.path)
            row.setToolTip(0, item.path)
            row.setToolTip(4, item.folder)
            for column in (1, 2, 3):
                row.setTextAlignment(column, Qt.AlignRight | Qt.AlignVCenter)
            colour = QColor(KIND_COLORS.get(item.kind, theme.VIOLET))
            colour.setAlphaF(0.85)
            row.setForeground(1, colour)
            rows.append(row)
        self.table.addTopLevelItems(rows)
        self.table.setUpdatesEnabled(True)

        shown = len(items)
        parts = [library.format_count(total, "file") + " matched"]
        if shown < total:
            parts.append(f"showing the first {shown:,} — narrow the search to see more")
        if stats.updated:
            parts.append("index updated " + _ago(stats.updated))
        if stats.unmatched:
            parts.append(
                f"{stats.unmatched:,} indexed outside your configured folders"
            )
        self.status.setText("  ·  ".join(parts))

    def _folder_label(self, folder: str) -> str:
        """Show a path relative to whichever media directory contains it."""
        for base in self._folders:
            base = base.rstrip("/")
            if folder == base:
                return os.path.basename(base) or base
            if folder.startswith(base + "/"):
                return os.path.basename(base) + folder[len(base):]
        home = os.path.expanduser("~")
        if folder.startswith(home + "/"):
            return "~" + folder[len(home):]
        return folder

    def _open_containing_folder(self, item: QTreeWidgetItem) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        path = item.data(0, Qt.UserRole)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


def _ago(timestamp: float) -> str:
    """A short relative time, e.g. "4 minutes ago"."""
    seconds = max(0, int(time.time() - timestamp))
    if seconds < 90:
        return "just now"
    for limit, divisor, noun in (
        (3600, 60, "minute"),
        (86400, 3600, "hour"),
        (86400 * 30, 86400, "day"),
    ):
        if seconds < limit:
            value = seconds // divisor
            return f"{value} {noun}{'' if value == 1 else 's'} ago"
    return time.strftime("%Y-%m-%d", time.localtime(timestamp))
