# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""The Problems view: what is wrong, whether the library is complete, the log."""

from __future__ import annotations

import os
import time
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import health, journal, theme
from .models import MediaKind
from .tasks import TaskError, run_async
from .widgets import GlassPanel, ViewSwitch

SEVERITY_COLORS = {"error": theme.RED, "warning": theme.AMBER, "info": theme.CYAN}
SEVERITY_GLYPHS = {"error": "✕", "warning": "!", "info": "i"}
LEVEL_COLORS = {"error": theme.RED, "fatal": theme.RED, "warn": theme.AMBER, "warning": theme.AMBER}

#: Log rows shown at once; the newest are kept.
LOG_ROWS = 1500

#: A refresh when the page is opened is skipped if the data is this fresh.
STALE_AFTER_S = 30.0


def _clock(stamp: float) -> str:
    return time.strftime("%d %b %H:%M:%S", time.localtime(stamp)) if stamp else ""


def _ago(stamp: float) -> str:
    seconds = max(0, int(time.time() - stamp))
    if seconds < 90:
        return "just now"
    for limit, divisor, noun in ((3600, 60, "minute"), (86400, 3600, "hour"), (86400 * 30, 86400, "day")):
        if seconds < limit:
            value = seconds // divisor
            return f"{value} {noun}{'' if value == 1 else 's'} ago"
    return time.strftime("%Y-%m-%d", time.localtime(stamp))


class ProblemCard(GlassPanel):
    """One grouped problem, with what it means and what to do about it."""

    rebuildRequested = Signal()

    def __init__(self, problem: journal.Problem, parent=None):
        super().__init__(parent, radius=14, fill=0.045, stroke=0.09)
        accent = SEVERITY_COLORS.get(problem.severity, theme.CYAN)
        if problem.resolved:
            accent = theme.GREEN

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 14, 14)
        layout.setSpacing(14)

        glyph = QLabel("✓" if problem.resolved else SEVERITY_GLYPHS.get(problem.severity, "i"))
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setFixedSize(26, 26)
        glyph.setStyleSheet(
            f"background: rgba(255,255,255,0.06); color: {accent};"
            "border-radius: 13px; font-weight: 800; font-size: 13px;"
        )
        layout.addWidget(glyph, 0, Qt.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(4)

        title = QLabel(problem.title)
        title.setWordWrap(True)
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        title.setStyleSheet("font-size: 14px; font-weight: 600;")
        column.addWidget(title)

        if problem.detail:
            detail = QLabel(problem.detail)
            detail.setWordWrap(True)
            detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
            detail.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")
            column.addWidget(detail)

        if problem.hint:
            hint = QLabel(problem.hint)
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {accent}; font-size: 12px;")
            column.addWidget(hint)

        if problem.last_seen:
            seen = QLabel(f"last seen {_ago(problem.last_seen)}")
            seen.setStyleSheet(f"color: {theme.TEXT_FAINT}; font-size: 11px;")
            column.addWidget(seen)

        layout.addLayout(column, 1)

        side = QVBoxLayout()
        side.setSpacing(6)
        if problem.count > 1:
            count = QLabel(f"×{problem.count:,}")
            count.setAlignment(Qt.AlignRight)
            count.setStyleSheet(f"color: {theme.TEXT_DIM}; font-weight: 600;")
            side.addWidget(count)
        if problem.paths and problem.kind == "unparseable":
            button = QPushButton("Copy file list")
            button.setCursor(QCursor(Qt.PointingHandCursor))
            button.clicked.connect(
                lambda: QGuiApplication.clipboard().setText("\n".join(problem.paths))
            )
            side.addWidget(button)
        if problem.kind == "unreachable" and problem.resolved:
            button = QPushButton("Rebuild library")
            button.setCursor(QCursor(Qt.PointingHandCursor))
            button.clicked.connect(self.rebuildRequested.emit)
            side.addWidget(button)
        side.addStretch(1)
        layout.addLayout(side)


class ProblemsPage(QWidget):
    """Problems, a disk-versus-index check, and the log."""

    rebuildRequested = Signal()
    #: (errors, warnings) found in the current run, for the nav badge.
    summaryChanged = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._unit = "minidlna.service"
        self._log_dir = ""
        self._db_path = ""
        self._folders: List[Tuple[str, MediaKind]] = []
        self._configured: List[str] = []

        self._loading = False
        self._loaded_at = 0.0
        self._data = None            # dict from _gather()
        self._checking = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.tabs = ViewSwitch(["Problems", "Library check", "Log"], self, padding=24)
        self.tabs.changed.connect(self._on_tab)
        controls.addWidget(self.tabs)

        self.scope = QComboBox(self)
        self.scope.addItem("Since last start", "run")
        self.scope.addItem("Last 7 days", "all")
        self.scope.currentIndexChanged.connect(self._render)
        controls.addWidget(self.scope)

        controls.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("subtitle")
        controls.addWidget(self.status)

        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.refresh_button.clicked.connect(self.refresh)
        controls.addWidget(self.refresh_button)
        outer.addLayout(controls)

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._build_problems())
        self.stack.addWidget(self._build_check())
        self.stack.addWidget(self._build_log())
        outer.addWidget(self.stack, 1)

    # ----------------------------------------------------------------- build

    def _build_problems(self) -> QWidget:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        container.setAttribute(Qt.WA_TranslucentBackground, True)
        self.problem_layout = QVBoxLayout(container)
        self.problem_layout.setContentsMargins(0, 0, 6, 0)
        self.problem_layout.setSpacing(9)
        self.problem_layout.addStretch(1)
        scroll.setWidget(container)
        return scroll

    def _build_check(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self.check_summary = QLabel(
            "Compares the files in each folder with what minidlna indexed."
        )
        self.check_summary.setObjectName("subtitle")
        self.check_summary.setWordWrap(True)
        top.addWidget(self.check_summary, 1)
        self.check_button = QPushButton("Run check", page)
        self.check_button.setObjectName("primary")
        self.check_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.check_button.clicked.connect(self.run_check)
        top.addWidget(self.check_button)
        layout.addLayout(top)

        self.check_table = QTreeWidget(page)
        self.check_table.setObjectName("libraryTable")
        self.check_table.setColumnCount(6)
        self.check_table.setHeaderLabels(
            ["Folder", "On disk", "Indexed", "Unreadable", "Not picked up", "Gone from disk"]
        )
        self.check_table.setRootIsDecorated(False)
        self.check_table.setUniformRowHeights(True)
        self.check_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.check_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.check_table.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 6):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.check_table.currentItemChanged.connect(self._show_check_details)
        layout.addWidget(self.check_table, 3)

        self.check_details = QTreeWidget(page)
        self.check_details.setObjectName("libraryTable")
        self.check_details.setHeaderHidden(True)
        self.check_details.setUniformRowHeights(True)
        self.check_details.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.check_details.itemActivated.connect(self._open_path_item)
        layout.addWidget(self.check_details, 2)
        return page

    def _build_log(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.log_search = QLineEdit(page)
        self.log_search.setPlaceholderText("Search the log…")
        self.log_search.setClearButtonEnabled(True)
        self.log_search.textChanged.connect(self._render_log)
        row.addWidget(self.log_search, 1)

        self.log_filter = QComboBox(page)
        self.log_filter.addItem("Without routine messages", "quiet")
        self.log_filter.addItem("Warnings and errors", "problems")
        self.log_filter.addItem("Errors only", "errors")
        self.log_filter.addItem("Everything", "all")
        self.log_filter.currentIndexChanged.connect(self._render_log)
        row.addWidget(self.log_filter)

        copy = QPushButton("Copy", page)
        copy.setCursor(QCursor(Qt.PointingHandCursor))
        copy.setToolTip("Copy the lines shown to the clipboard")
        copy.clicked.connect(self._copy_log)
        row.addWidget(copy)
        layout.addLayout(row)

        self.log_table = QTreeWidget(page)
        self.log_table.setObjectName("libraryTable")
        self.log_table.setColumnCount(3)
        self.log_table.setHeaderLabels(["Time", "Level", "Message"])
        self.log_table.setRootIsDecorated(False)
        self.log_table.setUniformRowHeights(True)
        self.log_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.log_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.log_table.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.log_table, 1)
        return page

    # --------------------------------------------------------------- context

    def set_context(
        self,
        unit: str,
        log_dir: str,
        db_path: str,
        folders: List[Tuple[str, MediaKind]],
    ) -> None:
        """Tell the page what to look at. ``folders`` are the saved ones."""
        self._unit = unit
        self._log_dir = log_dir
        self._db_path = db_path
        self._folders = list(folders)
        self._configured = [path for path, _ in folders]

    def activate(self) -> None:
        """The page was shown: refresh unless the data is fresh."""
        if time.monotonic() - self._loaded_at > STALE_AFTER_S:
            self.refresh()

    # ------------------------------------------------------------------ data

    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.refresh_button.setEnabled(False)
        self.status.setText("reading the log…")

        unit, log_dir, configured = self._unit, self._log_dir, list(self._configured)

        def gather():
            entries, error = journal.read_journal(unit, log_dir=log_dir)
            run = journal.current_run(entries)
            return {
                "entries": entries,
                "run": run,
                "error": error,
                "problems": {
                    "run": journal.analyse(run, configured=configured),
                    "all": journal.analyse(entries, configured=configured),
                },
                "failed": journal.failed_files(entries),
            }

        run_async(gather, self._on_gathered, self)

    def _on_gathered(self, result) -> None:
        self._loading = False
        self.refresh_button.setEnabled(True)
        if isinstance(result, TaskError):
            self.status.setText(f"could not read the log: {result}")
            return
        self._data = result
        self._loaded_at = time.monotonic()
        self._render()

        run_problems = result["problems"]["run"]
        errors = sum(1 for p in run_problems if p.severity == "error")
        warnings = sum(1 for p in run_problems if p.severity == "warning")
        self.summaryChanged.emit(errors, warnings)

    # ---------------------------------------------------------------- render

    def _scope_key(self) -> str:
        return self.scope.currentData() or "run"

    def _render(self) -> None:
        if not self._data:
            return
        data = self._data
        while self.problem_layout.count() > 1:
            item = self.problem_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        problems = data["problems"][self._scope_key()]
        if data["error"] and not data["entries"]:
            self._note(f"Could not read minidlna's log: {data['error']}", theme.RED)
        elif not problems:
            self._note("✓  Nothing looks wrong in the log.", theme.GREEN)
        for problem in problems:
            card = ProblemCard(problem, self)
            card.rebuildRequested.connect(self.rebuildRequested.emit)
            self.problem_layout.insertWidget(self.problem_layout.count() - 1, card)

        entries = data["run"] if self._scope_key() == "run" else data["entries"]
        self.status.setText(f"{len(entries):,} log lines")
        self._render_log()

    def _note(self, text: str, colour: str) -> None:
        label = QLabel(text)
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumHeight(120)
        label.setStyleSheet(f"color: {colour}; font-size: 15px; font-weight: 600;")
        self.problem_layout.insertWidget(self.problem_layout.count() - 1, label)

    def _visible_entries(self) -> List[journal.LogEntry]:
        if not self._data:
            return []
        entries = self._data["run"] if self._scope_key() == "run" else self._data["entries"]
        mode = self.log_filter.currentData()
        needle = self.log_search.text().strip().lower()

        def keep(entry: journal.LogEntry) -> bool:
            if mode == "errors" and entry.level not in ("error", "fatal"):
                return False
            if mode == "problems" and entry.level not in ("error", "fatal", "warn", "warning"):
                return False
            if mode == "quiet" and entry.is_routine:
                return False
            if needle and needle not in entry.text.lower() and needle not in entry.source.lower():
                return False
            return True

        return [entry for entry in entries if keep(entry)]

    def _render_log(self) -> None:
        entries = self._visible_entries()[-LOG_ROWS:]
        self.log_table.setUpdatesEnabled(False)
        self.log_table.clear()
        rows = []
        for entry in entries:
            row = QTreeWidgetItem([_clock(entry.time), entry.level or "info", entry.text])
            if entry.source:
                row.setToolTip(2, entry.source)
            colour = LEVEL_COLORS.get(entry.level)
            if colour:
                row.setForeground(1, QColor(colour))
            rows.append(row)
        self.log_table.addTopLevelItems(rows)
        self.log_table.setUpdatesEnabled(True)
        if rows:
            self.log_table.scrollToItem(rows[-1])

    def _copy_log(self) -> None:
        lines = [
            f"{_clock(e.time)}  {e.level or 'info':5}  {e.text}" for e in self._visible_entries()
        ]
        QGuiApplication.clipboard().setText("\n".join(lines))

    # ---------------------------------------------------------- library check

    def _on_tab(self, index: int) -> None:
        self.stack.setCurrentIndex(index)

    def run_check(self) -> None:
        if self._checking:
            return
        if not self._folders:
            self.check_summary.setText("There are no saved media folders to check.")
            return
        self._checking = True
        self.check_button.setEnabled(False)
        self.check_summary.setText("Checking… walking every folder can take a little while.")
        self.check_table.clear()
        self.check_details.clear()

        db_path, folders = self._db_path, list(self._folders)
        failed = dict(self._data["failed"]) if self._data else {}

        def work():
            indexed = health.indexed_paths(db_path)
            if indexed is None:
                return None
            return health.compare(folders, indexed, failed)

        run_async(work, self._on_checked, self)

    def _on_checked(self, result) -> None:
        self._checking = False
        self.check_button.setEnabled(True)
        if isinstance(result, TaskError):
            self.check_summary.setText(f"The check failed: {result}")
            return
        if result is None:
            self.check_summary.setText(
                "minidlna's database could not be read, so there is nothing to compare against."
            )
            return

        problems = 0
        rows = []
        for item in result:
            if not item.exists:
                row = QTreeWidgetItem(["⚠  " + item.path, "folder not found", "", "", "", ""])
                row.setForeground(0, QColor(theme.AMBER))
                problems += 1
            else:
                mark = "✓" if item.ok else "⚠"
                row = QTreeWidgetItem([
                    f"{mark}  {item.path}",
                    f"{item.on_disk:,}", f"{item.indexed:,}",
                    f"{item.unparseable:,}" if item.unparseable else "–",
                    f"{item.not_indexed:,}" if item.not_indexed else "–",
                    f"{item.stale:,}" if item.stale else "–",
                ])
                row.setForeground(0, QColor(theme.GREEN if item.ok else theme.AMBER))
                if not item.ok:
                    problems += 1
            for column in range(1, 6):
                row.setTextAlignment(column, Qt.AlignRight | Qt.AlignVCenter)
            row.setData(0, Qt.UserRole, item)
            rows.append(row)
        self.check_table.addTopLevelItems(rows)

        if problems:
            noun = "folder differs" if problems == 1 else "folders differ"
            self.check_summary.setText(f"{problems} {noun} from the index. Select one for details.")
        else:
            self.check_summary.setText(f"✓  All {len(result)} folders match the index.")
        if rows:
            first_bad = next((r for r in rows if r.data(0, Qt.UserRole) and not r.data(0, Qt.UserRole).ok), rows[0])
            self.check_table.setCurrentItem(first_bad)

    def _show_check_details(self, current: Optional[QTreeWidgetItem], _previous=None) -> None:
        self.check_details.clear()
        if current is None:
            return
        item: Optional[health.FolderHealth] = current.data(0, Qt.UserRole)
        if item is None:
            return

        def group(title: str, count: int, note: str, paths: List[str]) -> None:
            if not count:
                return
            parent = QTreeWidgetItem([f"{title} · {count:,}"])
            parent.setForeground(0, QColor(theme.AMBER))
            self.check_details.addTopLevelItem(parent)
            if note:
                QTreeWidgetItem(parent, [note]).setForeground(0, QColor(theme.TEXT_DIM))
            for path in paths:
                child = QTreeWidgetItem(parent, [path])
                child.setData(0, Qt.UserRole, path)
            if count > len(paths):
                QTreeWidgetItem(parent, [f"… and {count - len(paths):,} more"]).setForeground(
                    0, QColor(theme.TEXT_FAINT)
                )
            parent.setExpanded(True)

        if not item.exists:
            group("Folder not found", 1, "This directory does not exist right now. Is the drive mounted?", [])
            return
        reasons = ", ".join(f"“{why}” ×{n}" for why, n in item.reasons.items())
        group("Unreadable by minidlna", item.unparseable, reasons, item.examples.get("unparseable", []))
        extensions = ", ".join(f"{ext} ×{n}" for ext, n in sorted(item.missed_extensions.items(), key=lambda kv: -kv[1]))
        group(
            "Not picked up",
            item.not_indexed,
            (extensions + ". " if extensions else "") + "Not in the index and no error was logged. "
            "A rescan usually fixes this.",
            item.examples.get("not_indexed", []),
        )
        group(
            "In the index but gone from disk",
            item.stale,
            "Usually a drive that was unplugged, or files that were moved. A rescan clears them.",
            item.examples.get("stale", []),
        )
        if item.ok:
            parent = QTreeWidgetItem([f"✓  All {item.on_disk:,} files are indexed."])
            parent.setForeground(0, QColor(theme.GREEN))
            self.check_details.addTopLevelItem(parent)

    def _open_path_item(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, Qt.UserRole)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
