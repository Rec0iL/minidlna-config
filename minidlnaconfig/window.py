# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""The main application window."""

from __future__ import annotations

import os
import time
from typing import List, Optional

from PySide6.QtCore import (
    QObject,
    QUrl,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QFont, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QStackedWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import backups, library, privilege, resources, scan, theme
from .background import AuroraBackground
from .config import MinidlnaConfig
from .models import MediaFolder, MediaKind
from .service import ServiceController, guess_kinds, mount_points, readable_by
from .backupdialog import BackupDialog
from .devicespage import DevicesPage
from .drivewatch import DriveWatcher
from .librarypage import LibraryPage
from .prefs import Preferences
from .problemspage import ProblemsPage
from .scanwatch import ScanWatcher
from .settingspage import SettingsPage
from .tray import TrayController
from .widgets import FolderRow, GlassPanel, StatusPill, ToastHost, ViewSwitch

CORNER_RADIUS = 18
RESIZE_MARGIN = 6


class _TaskSignals(QObject):
    done = Signal(object)


class _Task(QRunnable):
    """Runs a blocking privileged call off the UI thread.

    pkexec shows a modal authentication dialog that can sit there for as long
    as the user takes; doing that on the GUI thread would freeze the window.

    The signals object is supplied by the caller and owned by the window, not
    by this runnable. QThreadPool deletes a runnable as soon as run() returns,
    which would take a signals object owned here with it - and a queued signal
    whose sender has been destroyed is silently dropped, so the result would
    never arrive on the GUI thread.
    """

    def __init__(self, function, signals: _TaskSignals):
        super().__init__()
        self.function = function
        self.signals = signals

    def run(self) -> None:
        try:
            result = self.function()
        except Exception as exc:                      # pragma: no cover - defensive
            result = privilege.Result(False, "Unexpected error", detail=str(exc))
        self.signals.done.emit(result)


class TitleBar(QWidget):
    """Custom title bar for the frameless window."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedHeight(46)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 0, 10, 0)
        layout.setSpacing(8)

        mark = QLabel()
        icon = resources.app_icon()
        if icon.isNull():
            mark.setText("◈")
            mark.setStyleSheet(f"color: {theme.VIOLET}; font-size: 15px;")
        else:
            # QIcon renders the SVG at the right density for this screen.
            mark.setPixmap(icon.pixmap(QSize(18, 18)))
        layout.addWidget(mark)

        name = QLabel("MiniDLNA")
        font = QFont(name.font())
        font.setWeight(QFont.DemiBold)
        font.setPointSizeF(10.5)
        name.setFont(font)
        name.setStyleSheet(f"color: {theme.TEXT_DIM}; letter-spacing: 0.4px;")
        layout.addWidget(name)

        self.dirty_dot = QLabel("●")
        self.dirty_dot.setStyleSheet(f"color: {theme.AMBER}; font-size: 10px;")
        self.dirty_dot.setToolTip("You have unsaved changes")
        self.dirty_dot.setVisible(False)
        layout.addWidget(self.dirty_dot)

        layout.addStretch(1)

        for glyph, slot, name_id in (
            ("—", parent.showMinimized, "windowMin"),
            ("▢", parent.toggle_maximised, "windowMax"),
            ("✕", parent.close, "windowClose"),
        ):
            button = QPushButton(glyph)
            button.setObjectName(name_id if name_id == "windowClose" else "iconGhost")
            if name_id == "windowClose":
                button.setStyleSheet(
                    "QPushButton { background: transparent; border: none;"
                    "border-radius: 8px; padding: 6px; color: rgba(232,238,255,0.62); }"
                    f"QPushButton:hover {{ background: {theme.RED}; color: white; }}"
                )
            button.setFixedSize(QSize(30, 30))
            button.setCursor(QCursor(Qt.PointingHandCursor))
            button.clicked.connect(slot)
            layout.addWidget(button)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.window().toggle_maximised()


class MainWindow(QWidget):
    """Everything the app does, in one screen."""

    def __init__(self, config_path: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("MiniDLNA Configuration")
        self.resize(1040, 740)
        self.setMinimumSize(880, 540)
        self.setAcceptDrops(True)
        QApplication.setQuitOnLastWindowClosed(False)

        native_frame = os.environ.get("MINIDLNA_GUI_NATIVE_FRAME", "") == "1"
        self._frameless = not native_frame
        if self._frameless:
            self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        self.config = MinidlnaConfig.load(config_path)
        self.service = ServiceController()
        self._service_user = self.service.service_user()
        self._dirty = False
        self._baseline: tuple = ()
        self._close_after_apply = False
        self._busy = False
        self._rows: List[FolderRow] = []
        self._pool = QThreadPool.globalInstance()
        self.prefs = Preferences()
        self.tray: Optional[TrayController] = None
        self._quitting = False
        self._tray_hint_shown = False
        self._was_active: Optional[bool] = None
        self._expect_down_until = 0.0
        self._scan_text = ""

        self.background = AuroraBackground(self)
        if self._frameless:
            self.background.set_radius(CORNER_RADIUS)

        self._build()
        self.toasts = ToastHost(self)

        self._populate()
        self._reset_baseline()
        self._refresh_status()
        self._refresh_index_counts()

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(5000)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

        self._last_library_refresh = 0.0
        self.scan_watcher = ScanWatcher(self.service, self._scan_source, self)
        self.scan_watcher.progress.connect(self._on_scan_progress)
        self.scan_watcher.finished.connect(self._on_scan_finished)
        self.scan_watcher.start()

        self.drive_watcher = DriveWatcher(
            self.service,
            lambda: [folder.path for folder in self.config.folders],
            lambda: self.service.unit,
            self,
        )
        self.drive_watcher.appeared.connect(self._on_drives_appeared)
        self.drive_watcher.vanished.connect(self._on_drives_vanished)
        self.drive_watcher.returned.connect(self._on_drives_returned)
        self.drive_watcher.start()

        self._setup_tray()

        self._sync_pages()
        QTimer.singleShot(1500, self.problems_page.refresh)

        self._install_shortcuts()

    # ---------------------------------------------------------------- layout

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        if self._frameless:
            self.title_bar = TitleBar(self)
            root.addWidget(self.title_bar)
        else:
            self.title_bar = None

        body = QVBoxLayout()
        body.setContentsMargins(22, 8 if self._frameless else 20, 22, 18)
        body.setSpacing(14)
        root.addLayout(body, 1)

        body.addWidget(self._build_header())
        body.addWidget(self._build_banner())
        body.addLayout(self._build_toolbar())
        body.addWidget(self._build_pages(), 1)
        body.addWidget(self._build_footer())

    def _build_header(self) -> QWidget:
        panel = GlassPanel(self, radius=16, fill=0.05)
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(20, 16, 16, 16)
        layout.setSpacing(14)

        text_column = QVBoxLayout()
        text_column.setSpacing(3)

        heading = QLabel("Media Library")
        heading.setObjectName("title")
        text_column.addWidget(heading)

        self.subtitle = QLabel()
        self.subtitle.setObjectName("subtitle")
        self.subtitle.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text_column.addWidget(self.subtitle)

        self.scan_box = QWidget(panel)
        scan_layout = QHBoxLayout(self.scan_box)
        scan_layout.setContentsMargins(0, 4, 0, 0)
        scan_layout.setSpacing(10)
        self.scan_bar = QProgressBar(self.scan_box)
        self.scan_bar.setObjectName("scanBar")
        self.scan_bar.setTextVisible(False)
        self.scan_bar.setFixedHeight(6)
        self.scan_bar.setRange(0, 1000)
        scan_layout.addWidget(self.scan_bar, 1)
        self.scan_label = QLabel(self.scan_box)
        self.scan_label.setObjectName("subtitle")
        scan_layout.addWidget(self.scan_label)
        self.scan_box.setVisible(False)
        text_column.addWidget(self.scan_box)

        layout.addLayout(text_column, 1)

        self.status_pill = StatusPill(panel)
        layout.addWidget(self.status_pill, 0, Qt.AlignVCenter)

        self.service_button = QPushButton("Server  ⌄")
        self.service_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.service_button.setToolTip("Start, stop or rebuild the media library")
        self.service_button.clicked.connect(self._show_service_menu)
        layout.addWidget(self.service_button, 0, Qt.AlignVCenter)

        return panel

    def _build_banner(self) -> QWidget:
        """A dismissible notice, shown when a skipped drive has come back."""
        self.banner = GlassPanel(self, radius=14, fill=0.07, stroke=0.20)
        layout = QHBoxLayout(self.banner)
        layout.setContentsMargins(16, 10, 10, 10)
        layout.setSpacing(12)

        self.banner_label = QLabel()
        self.banner_label.setWordWrap(True)
        layout.addWidget(self.banner_label, 1)

        self.banner_action = QPushButton("Rebuild library")
        self.banner_action.setObjectName("primary")
        self.banner_action.setCursor(QCursor(Qt.PointingHandCursor))
        self.banner_action.clicked.connect(self._banner_rebuild)
        layout.addWidget(self.banner_action)

        dismiss = QPushButton("Dismiss")
        dismiss.setCursor(QCursor(Qt.PointingHandCursor))
        dismiss.clicked.connect(lambda: self.banner.setVisible(False))
        layout.addWidget(dismiss)

        self.banner.setVisible(False)
        return self.banner

    def _build_toolbar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(12)

        self.view_switch = ViewSwitch(
            ["Folders", "Library", "Problems", "Devices", "Settings"], self, padding=26
        )
        self.view_switch.setToolTip(
            "Folders: what minidlna is told to share\n"
            "Library: what it has actually indexed\n"
            "Problems: what the log says is wrong, and a disk-versus-index check\n"
            "Devices: who is connected\n"
            "Settings: server options and app preferences"
        )
        self.view_switch.changed.connect(self._on_view_changed)
        layout.addWidget(self.view_switch)

        self.count_label = QLabel()
        self.count_label.setObjectName("sectionLabel")
        layout.addWidget(self.count_label)
        layout.addStretch(1)

        self.add_button = QPushButton("＋  Add folders")
        self.add_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.add_button.setToolTip("Add media folders  (Ctrl+O)\nYou can also drag folders into this window")
        self.add_button.clicked.connect(self._add_folders)
        layout.addWidget(self.add_button)

        return layout

    def _build_list(self) -> QWidget:
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        container = QWidget()
        container.setAttribute(Qt.WA_TranslucentBackground, True)
        self.list_layout = QVBoxLayout(container)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(9)
        self.list_layout.addStretch(1)

        self.empty_state = self._build_empty_state()
        self.list_layout.insertWidget(0, self.empty_state)

        self.scroll.setWidget(container)
        return self.scroll

    def _build_pages(self) -> QWidget:
        self.stack = QStackedWidget(self)
        self.stack.addWidget(self._build_list())

        self.library_page = LibraryPage(self)
        self.library_page.rebuildRequested.connect(lambda: self._service("rescan"))
        self.stack.addWidget(self.library_page)

        self.problems_page = ProblemsPage(self)
        self.problems_page.rebuildRequested.connect(lambda: self._service("rescan"))
        self.problems_page.summaryChanged.connect(self._on_problem_summary)
        self.stack.addWidget(self.problems_page)

        self.devices_page = DevicesPage(self)
        self.stack.addWidget(self.devices_page)

        self.settings_page = SettingsPage(self.prefs, self)
        self.settings_page.changed.connect(self._recompute_dirty)
        self.settings_page.preferenceChanged.connect(self._on_preference)
        self.settings_page.backupsRequested.connect(self._show_backups)
        self.stack.addWidget(self.settings_page)
        return self.stack

    def _on_view_changed(self, index: int) -> None:
        previous = self.stack.currentIndex()
        if previous == 3 and index != 3:
            self.devices_page.deactivate()
        self.stack.setCurrentIndex(index)
        self.add_button.setVisible(index == 0)
        self.count_label.setVisible(index == 0)
        self._sync_pages()
        if index == 1:
            self._sync_library_source()
            self.library_page.refresh()
        elif index == 2:
            self.problems_page.activate()
        elif index == 3:
            self.devices_page.activate()
        elif index == 4:
            self.settings_page.set_backup_count(len(backups.list_backups(self.config.path)))

    def _sync_pages(self) -> None:
        """Tell the pages that read the log or the server what to look at."""
        db_path = library.database_path(self.config.db_dir)
        log_dir = self.config.setting("log_dir") or ""
        self.problems_page.set_context(
            self.service.unit, log_dir, db_path,
            [(folder.path, folder.kinds) for folder in self.config.folders],
        )
        self.devices_page.set_context(self.config.port, self.service.unit, log_dir)

    def _on_problem_summary(self, errors: int, warnings: int) -> None:
        level = "error" if errors else "warning" if warnings else ""
        self.view_switch.set_badge(2, level)

    def _sync_library_source(self) -> None:
        self.library_page.set_source(
            library.database_path(self.config.db_dir),
            [row.folder.path for row in self._rows],
        )

    def _refresh_index_counts(self) -> None:
        """Annotate each folder row with what minidlna has indexed there."""
        self._apply_index_stats(library.read_stats(
            library.database_path(self.config.db_dir),
            [row.folder.path for row in self._rows],
        ))

    def _apply_index_stats(self, stats: "library.LibraryStats") -> None:
        if not stats.available:
            for row in self._rows:
                row.set_index_stats(None)
            return
        by_path = {folder.path: folder for folder in stats.folders}
        for row in self._rows:
            folder = by_path.get(row.folder.path)
            if folder is None:
                row.set_index_stats(None)
            else:
                row.set_index_stats(folder.files, folder.size)

    def _scan_source(self):
        """What the scan watcher needs, evaluated on the UI thread."""
        return (
            library.database_path(self.config.db_dir),
            [row.folder.path for row in self._rows],
            [(folder.path, folder.kinds) for folder in self.config.folders],
        )

    def _on_scan_progress(self, state: "scan.ScanState", stats: "library.LibraryStats") -> None:
        fraction = state.fraction
        if fraction is None:
            self.scan_bar.setRange(0, 0)
            found = library.format_count(state.indexed, "file")
            self.scan_label.setText(f"Scanning · {found} so far")
        else:
            self.scan_bar.setRange(0, 1000)
            self.scan_bar.setValue(int(fraction * 1000))
            self.scan_label.setText(
                f"Scanning · {state.indexed:,} of ~{state.expected:,} files  ·  "
                f"{int(fraction * 100)}%"
            )
        self.scan_box.setVisible(True)
        self._scan_text = (
            f"scanning · {int(fraction * 100)}%" if fraction is not None else "scanning"
        )

        self._apply_index_stats(stats)
        # Re-reading the Library view rebuilds its table, so do not do it on
        # every tick of a scan.
        now = time.monotonic()
        if self.stack.currentIndex() == 1 and now - self._last_library_refresh >= 3.0:
            self._last_library_refresh = now
            self.library_page.set_scanning(True)
            self.library_page.apply_stats(stats)

    def _on_scan_finished(self, stats: "library.LibraryStats", announce: bool) -> None:
        self.scan_box.setVisible(False)
        self._scan_text = ""
        QTimer.singleShot(1000, self.problems_page.refresh)
        self._apply_index_stats(stats)
        self.library_page.set_scanning(False)
        self.library_page.apply_stats(stats)
        if announce and stats.available:
            message = f"Library scan finished · {library.format_count(stats.files, 'file')} indexed"
            self.toasts.post(message, "success")
            self._notify("Library scan finished", message.split(" · ", 1)[1])

    def _build_empty_state(self) -> QWidget:
        panel = GlassPanel(self, radius=16, fill=0.025, stroke=0.07)
        panel.setMinimumHeight(200)
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)
        layout.addStretch(1)

        icon = QLabel("⤓")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(f"color: {theme.TEXT_FAINT}; font-size: 34px;")
        layout.addWidget(icon)

        title = QLabel("No media folders yet")
        title.setObjectName("emptyTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        hint = QLabel("Drag folders here, or use “Add folders” above")
        hint.setObjectName("emptyHint")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

        layout.addStretch(1)
        return panel

    def _build_footer(self) -> QWidget:
        panel = QWidget(self)
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(10)

        self.footer_label = QLabel()
        self.footer_label.setObjectName("subtitle")
        self.footer_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.footer_label, 1)

        self.revert_button = QPushButton("Revert")
        self.revert_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.revert_button.setToolTip("Discard your changes and reload the file from disk")
        self.revert_button.clicked.connect(self._revert)
        layout.addWidget(self.revert_button)

        self.apply_button = QPushButton("Apply  ⏎")
        self.apply_button.setObjectName("primary")
        self.apply_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.apply_button.setToolTip(
            "Save the configuration and restart minidlna  (Ctrl+S)\n"
            "Right-click for other save options"
        )
        self.apply_button.setContextMenuPolicy(Qt.CustomContextMenu)
        self.apply_button.customContextMenuRequested.connect(self._show_apply_menu)
        self.apply_button.clicked.connect(lambda: self._apply(restart=True))
        layout.addWidget(self.apply_button)

        return panel

    def _install_shortcuts(self) -> None:
        for keys, slot in (
            (QKeySequence.Open, self._add_folders),
            (QKeySequence.Save, lambda: self._apply(restart=True)),
            (QKeySequence.Refresh, self._revert),
            (QKeySequence.Quit, self.quit_app),
        ):
            action = QAction(self)
            action.setShortcut(keys)
            action.triggered.connect(slot)
            self.addAction(action)

    # -------------------------------------------------------------- content

    def _populate(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []

        for folder in self.config.folders:
            self._add_row(folder)

        self.settings_page.load(self.config.setting_values())
        self._sync_chrome()

    def _add_row(self, folder: MediaFolder) -> FolderRow:
        row = FolderRow(folder, self)
        row.changed.connect(self._on_row_changed)
        row.removeRequested.connect(self._remove_row)
        row.browseRequested.connect(self._rebrowse_row)
        self.list_layout.insertWidget(self.list_layout.count() - 1, row)
        self._rows.append(row)
        self._check_row_health(row)
        return row

    def _check_row_health(self, row: FolderRow) -> None:
        exists = os.path.isdir(row.folder.path)
        readable = readable_by(row.folder.path, self._service_user) if exists else None
        row.apply_health(exists, readable)

    def _on_row_changed(self) -> None:
        row = self.sender()
        if isinstance(row, FolderRow):
            self._check_row_health(row)
        self._recompute_dirty()

    def _remove_row(self, row: FolderRow) -> None:
        index = self._rows.index(row)
        folder = row.folder
        self._rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._recompute_dirty()
        self._sync_chrome()

        def undo():
            restored = FolderRow(MediaFolder(folder.kinds, folder.path), self)
            restored.changed.connect(self._on_row_changed)
            restored.removeRequested.connect(self._remove_row)
            restored.browseRequested.connect(self._rebrowse_row)
            self.list_layout.insertWidget(index, restored)
            self._rows.insert(index, restored)
            self._check_row_health(restored)
            self._recompute_dirty()

        self.toasts.post(f"Removed “{folder.name}”", "info", "Undo", undo)

    def _rebrowse_row(self, row: FolderRow) -> None:
        start = row.folder.path if os.path.isdir(row.folder.path) else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        if chosen:
            row.set_path(chosen)

    def _picker_places(self) -> List:
        """Sidebar shortcuts for the folder picker.

        Your home directory and media folders first, then every mounted drive,
        so external and secondary disks are one click away instead of being
        buried under /run/media.
        """
        from PySide6.QtCore import QStandardPaths, QUrl

        places = []

        def add(path: str) -> None:
            if path and os.path.isdir(path):
                url = QUrl.fromLocalFile(path)
                if url not in places:
                    places.append(url)

        add(os.path.expanduser("~"))
        for location in (
            QStandardPaths.MoviesLocation,
            QStandardPaths.MusicLocation,
            QStandardPaths.PicturesLocation,
            QStandardPaths.DownloadLocation,
        ):
            add(QStandardPaths.writableLocation(location))

        for _label, path in mount_points():
            add(path)

        return places

    def _add_folders(self) -> None:
        """Pick one or more folders.

        Qt's native directory chooser only returns one folder at a time, so we
        use the non-native dialog in multi-select mode - adding six folders in
        one pass instead of six round trips through the old dialog chain.
        """
        dialog = QFileDialog(self, "Add media folders", os.path.expanduser("~"))
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setSidebarUrls(self._picker_places())
        dialog.setViewMode(QFileDialog.Detail)
        dialog.setStyleSheet(theme.DIALOG_QSS)
        dialog.resize(940, 620)

        view = dialog.findChild(QWidget, "listView")
        if view is not None:
            from PySide6.QtWidgets import QAbstractItemView
            view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        tree = dialog.findChild(QWidget, "treeView")
        if tree is not None:
            from PySide6.QtWidgets import QAbstractItemView
            tree.setSelectionMode(QAbstractItemView.ExtendedSelection)

        if dialog.exec():
            self._accept_paths(dialog.selectedFiles())

    def _accept_paths(self, paths: List[str]) -> None:
        """Add directories, guessing their media types, skipping duplicates."""
        existing = {os.path.normpath(row.folder.path) for row in self._rows}
        added, skipped = 0, 0

        for path in paths:
            if not os.path.isdir(path):
                continue
            if os.path.normpath(path) in existing:
                skipped += 1
                continue
            existing.add(os.path.normpath(path))
            folder = MediaFolder(guess_kinds(path), path)
            self._add_row(folder)
            added += 1

        if added:
            self._recompute_dirty()
            self._sync_chrome()
            QTimer.singleShot(0, self._scroll_to_bottom)
            noun = "folder" if added == 1 else "folders"
            self.toasts.post(
                f"Added {added} {noun} · media types detected — adjust the chips to change them",
                "success",
            )
        if skipped:
            noun = "folder is" if skipped == 1 else "folders are"
            self.toasts.post(f"{skipped} {noun} already in the list", "warning")

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ---------------------------------------------------------------- chrome

    def _sync_chrome(self) -> None:
        count = len(self._rows)
        self.empty_state.setVisible(count == 0)
        self.count_label.setText(
            "MEDIA FOLDERS" if count == 0 else f"MEDIA FOLDERS · {count}"
        )

        if self.config.path:
            location = self.config.path
            if self.config.needs_root():
                location += "  ·  needs administrator access to save"
            self.footer_label.setText(location)
        else:
            self.footer_label.setText("No minidlna.conf found — use Apply to choose where to save")

        port = self.config.port
        name = self.config.friendly_name or "this machine"
        self.subtitle.setText(f"Serving {name} on port {port}")

        self.apply_button.setEnabled(self._dirty and not self._busy)
        self.revert_button.setEnabled(self._dirty and not self._busy)
        if self.title_bar:
            self.title_bar.dirty_dot.setVisible(self._dirty)

    def _snapshot(self) -> tuple:
        """The folder list and settings as comparable plain data."""
        folders = tuple((row.folder.kinds.letters, row.folder.path) for row in self._rows)
        return folders, tuple(sorted(self.settings_page.values().items()))

    def _reset_baseline(self) -> None:
        """Record the current state as matching what is on disk."""
        self._baseline = self._snapshot()
        self._dirty = False
        self._sync_chrome()

    def _recompute_dirty(self) -> None:
        """Compare against the saved state rather than tracking interactions.

        Toggling a chip on and back off leaves the configuration identical, so
        it must not leave the window claiming to have unsaved changes.
        """
        self._dirty = self._snapshot() != self._baseline
        self._sync_chrome()

    def _refresh_status(self) -> None:
        status = self.service.status()
        self.status_pill.set_state(status.active, status.summary)

        if self.tray is not None:
            text = status.summary
            if status.active and self._scan_text:
                text += f" · {self._scan_text}"
            self.tray.set_status(text)

        if (
            self._was_active
            and not status.active
            and time.monotonic() > self._expect_down_until
        ):
            self.toasts.post("minidlna stopped running", "warning", timeout=7000)
            self._notify("minidlna stopped", "The server is no longer running.")
        self._was_active = status.active

    # ----------------------------------------------------------- operations

    def _current_folders(self) -> List[MediaFolder]:
        return [row.folder for row in self._rows]

    def _show_apply_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Save and restart minidlna", lambda: self._apply(restart=True))
        menu.addAction("Save without restarting", lambda: self._apply(restart=False))
        menu.addSeparator()
        rebuild = menu.addAction(
            "Save and rebuild media library", lambda: self._apply(rescan=True)
        )
        rebuild.setToolTip("Clears the media database and rescans from scratch")
        menu.exec(QCursor.pos())

    def _show_service_menu(self) -> None:
        status = self.service.status()
        menu = QMenu(self)
        if status.active:
            menu.addAction("Restart", lambda: self._service("restart"))
            menu.addAction("Stop", lambda: self._service("stop"))
        else:
            menu.addAction("Start", lambda: self._service("start"))
        menu.addSeparator()
        menu.addAction("Rebuild media library", lambda: self._service("rescan"))
        menu.addSeparator()
        open_action = menu.addAction("Open web interface")
        open_action.setEnabled(status.active)
        open_action.triggered.connect(self._open_web_interface)
        menu.exec(self.service_button.mapToGlobal(self.service_button.rect().bottomLeft()))

    def _open_web_interface(self) -> None:
        # minidlna rejects requests whose Host is a name ("DNS rebinding
        # attack suspected"), so address it by IP.
        QDesktopServices.openUrl(QUrl(f"http://127.0.0.1:{self.config.port}"))

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = busy
        self.apply_button.setText(label or "Apply  ⏎")
        self.service_button.setEnabled(not busy)
        self._sync_chrome()

    def _apply(self, restart: bool = True, rescan: bool = False) -> None:
        if self._busy or not self._dirty:
            return

        if not self.config.path:
            chosen, _ = QFileDialog.getSaveFileName(
                self, "Save minidlna configuration as", "/etc/minidlna.conf",
                "Configuration files (*.conf)",
            )
            if not chosen:
                return
            self.config.path = chosen

        folders = self._current_folders()
        settings = self.settings_page.values()

        notes = self.settings_page.warnings_for(self.config.setting_values(), settings)
        if notes and not self._confirm_settings(notes):
            self._quitting = False
            self._close_after_apply = False
            return

        text = self.config.render(folders, settings)
        self._write_config(text, restart=restart, rescan=rescan)

    def _confirm_settings(self, notes: List[str]) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Apply these changes?")
        box.setText("Some of the changes you made have side effects.")
        box.setInformativeText("\n\n".join(notes))
        apply_button = box.addButton("Apply anyway", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is apply_button

    def _write_config(
        self, text: str, restart: bool, rescan: bool, reload_rows: bool = False
    ) -> None:
        """Save ``text`` as the configuration, elevating only if needed."""
        # Fast path: if we own the file there is no reason to involve polkit.
        if not self.config.needs_root() and not restart and not rescan:
            try:
                backup = self.config.write_directly(text)
            except OSError as exc:
                self.toasts.post(f"Could not save: {exc}", "error", timeout=7000)
                return
            self._after_write(text, reload_rows)
            message = "Saved" + (f" · backup {os.path.basename(backup)}" if backup else "")
            self.toasts.post(message, "success")
            return

        self._set_busy(True, "Applying…")
        unit = self.service.unit
        db_dir = self.config.db_dir
        path = self.config.path
        if restart or rescan:
            self._expect_down_until = time.monotonic() + 20

        def work():
            return privilege.apply_config(
                text, path, restart=restart, rescan=rescan, unit=unit, db_dir=db_dir
            )

        signals = _TaskSignals(self)
        signals.done.connect(
            lambda result: self._on_applied(result, text, restart, rescan, reload_rows)
        )
        signals.done.connect(lambda _: signals.deleteLater())
        self._pool.start(_Task(work, signals))

    def _after_write(self, text: str, reload_rows: bool) -> None:
        """The file on disk now holds ``text``; bring the window in line."""
        self.config.adopt(text)
        if reload_rows:
            self._populate()
            self._refresh_index_counts()
        else:
            self.settings_page.load(self.config.setting_values())
        self.drive_watcher.forget()
        self._sync_pages()
        self._reset_baseline()

    def _on_applied(
        self, result: privilege.Result, text: str,
        restart: bool, rescan: bool, reload_rows: bool = False,
    ) -> None:
        self._set_busy(False)
        if result.ok:
            self._after_write(text, reload_rows)
            self.toasts.post(result.message or "Configuration applied", "success")
            QTimer.singleShot(600, self._refresh_status)
            if restart or rescan:
                self.scan_watcher.kick()
                QTimer.singleShot(2500, self.problems_page.refresh)
            if self._close_after_apply:
                self._close_after_apply = False
                QTimer.singleShot(0, self.close)
                return
        else:
            # A failed or cancelled save must not close the window and lose
            # the edits it was asked to save.
            self._close_after_apply = False
            self._quitting = False
        if result.cancelled:
            self.toasts.post("Cancelled — nothing was changed", "info")
        elif not result.ok:
            detail = f"\n{result.detail}" if result.detail else ""
            self.toasts.post(f"{result.message}{detail}", "error", timeout=9000)

    def _service(self, action: str) -> None:
        if self._busy:
            return
        self._set_busy(True, "Working…")
        if action in ("stop", "restart", "rescan"):
            self._expect_down_until = time.monotonic() + 20
        unit = self.service.unit
        db_dir = self.config.db_dir

        signals = _TaskSignals(self)
        signals.done.connect(lambda result: self._on_service_done(result, action))
        signals.done.connect(lambda _: signals.deleteLater())
        self._pool.start(_Task(lambda: privilege.control_service(action, unit, db_dir), signals))

    def _on_service_done(self, result: privilege.Result, action: str = "") -> None:
        self._set_busy(False)
        if result.ok:
            self.toasts.post(result.message or "Done", "success")
            if action in ("start", "restart", "rescan"):
                self.scan_watcher.kick()
                QTimer.singleShot(2500, self.problems_page.refresh)
        elif result.cancelled:
            self.toasts.post("Cancelled", "info")
        else:
            detail = f"\n{result.detail}" if result.detail else ""
            self.toasts.post(f"{result.message}{detail}", "error", timeout=9000)
        QTimer.singleShot(700, self._refresh_status)

    def _revert(self) -> None:
        self.config = MinidlnaConfig.load(self.config.path)
        self._populate()
        self._reset_baseline()
        self._refresh_index_counts()
        self.toasts.post("Reloaded from disk", "info")

    # ------------------------------------------------------ tray and drives

    def _setup_tray(self) -> None:
        if self.tray is not None or not TrayController.is_available():
            return
        self.tray = TrayController(resources.app_icon(), self)
        self.tray.showRequested.connect(self._show_from_tray)
        self.tray.restartRequested.connect(lambda: self._service("restart"))
        self.tray.rebuildRequested.connect(lambda: self._service("rescan"))
        self.tray.openWebRequested.connect(self._open_web_interface)
        self.tray.quitRequested.connect(self.quit_app)
        self.tray.set_visible(self.prefs.get("tray_icon"))

    def _on_preference(self, key: str, value: bool) -> None:
        if key == "tray_icon":
            if value and self.tray is None:
                self._setup_tray()
            if value and self.tray is None:
                self.toasts.post("This desktop has no system tray to show an icon in", "warning")
            elif self.tray is not None:
                self.tray.set_visible(value)
        elif key == "close_to_tray" and value and not (self.tray and self.tray.is_visible()):
            self.toasts.post("Turn on the tray icon too, or the window cannot be reached", "warning")

    def _hide_to_tray(self) -> bool:
        return bool(
            self.tray is not None
            and self.tray.is_visible()
            and self.prefs.get("close_to_tray")
        )

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if self.stack.currentIndex() == 3:
            self.devices_page.activate()

    def _notify(self, title: str, message: str) -> None:
        """A desktop notification, when the tray is there and they are wanted."""
        if self.tray is not None and self.prefs.get("notifications"):
            self.tray.notify(title, message)

    def quit_app(self) -> None:
        """Really quit, rather than hiding to the tray."""
        self._quitting = True
        if not self.isVisible():
            self._show_from_tray()
        self.close()

    @staticmethod
    def _names(paths: List[str]) -> str:
        return ", ".join(os.path.basename(p.rstrip("/")) or p for p in paths)

    def _recheck_health(self) -> None:
        for row in self._rows:
            self._check_row_health(row)

    def _on_drives_appeared(self, paths: List[str]) -> None:
        self._recheck_health()
        names = self._names(paths)
        if self.prefs.get("auto_restart_on_drive") and not self._busy:
            self.toasts.post(f"{names} is available again — rebuilding the library", "info")
            self._notify("Drive available", f"{names} is back. Rebuilding the library.")
            self._service("rescan")
            return
        plural = len(paths) > 1
        self.banner_label.setText(
            f"{names} {'were' if plural else 'was'} not available when minidlna started, "
            f"but {'are' if plural else 'is'} now. Rebuild the library to include "
            f"{'them' if plural else 'it'}."
        )
        self.banner.setVisible(True)
        self._notify("Drive available", f"{names} is back. Rebuild the library to include it.")

    def _on_drives_vanished(self, paths: List[str]) -> None:
        self._recheck_health()
        names = self._names(paths)
        self.toasts.post(f"{names} is no longer available", "warning", timeout=7000)
        self._notify("Media folder missing", f"{names} is no longer available.")

    def _on_drives_returned(self, paths: List[str]) -> None:
        self._recheck_health()

    def _banner_rebuild(self) -> None:
        self.banner.setVisible(False)
        self._service("rescan")

    # --------------------------------------------------------------- backups

    def _show_backups(self) -> None:
        if not self.config.path:
            return
        found = backups.list_backups(self.config.path)
        if not found:
            self.toasts.post("There are no earlier versions of this file yet", "info")
            return
        dialog = BackupDialog("".join(self.config.lines), found, self)
        dialog.restoreRequested.connect(self._restore_text)
        dialog.exec()

    def _restore_text(self, text: str) -> None:
        if self._busy:
            return
        if self._dirty:
            answer = QMessageBox.question(
                self, "Discard unsaved changes?",
                "Restoring replaces the configuration, including the changes you "
                "have not saved yet. Continue?",
            )
            if answer != QMessageBox.Yes:
                return
        self._write_config(text, restart=True, rescan=False, reload_rows=True)

    # -------------------------------------------------------- drag and drop

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(
            url.isLocalFile() and os.path.isdir(url.toLocalFile())
            for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile() and os.path.isdir(url.toLocalFile())
        ]
        if paths:
            self._accept_paths(paths)
            event.acceptProposedAction()

    # ------------------------------------------------------ window plumbing

    def toggle_maximised(self) -> None:
        if self.isMaximized():
            self.showNormal()
            if self._frameless:
                self.background.set_radius(CORNER_RADIUS)
        else:
            if self._frameless:
                self.background.set_radius(0)
            self.showMaximized()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.background.setGeometry(self.rect())
        self.background.lower()
        if hasattr(self, "toasts"):
            self.toasts._relayout()

    def _edges_at(self, position) -> Qt.Edges:
        if self.isMaximized():
            return Qt.Edges()
        edges = Qt.Edges()
        if position.x() <= RESIZE_MARGIN:
            edges |= Qt.LeftEdge
        if position.x() >= self.width() - RESIZE_MARGIN:
            edges |= Qt.RightEdge
        if position.y() <= RESIZE_MARGIN:
            edges |= Qt.TopEdge
        if position.y() >= self.height() - RESIZE_MARGIN:
            edges |= Qt.BottomEdge
        return edges

    def mouseMoveEvent(self, event):
        if self._frameless:
            edges = self._edges_at(event.position().toPoint())
            self.setCursor(self._cursor_for(edges))
        super().mouseMoveEvent(event)

    @staticmethod
    def _cursor_for(edges: Qt.Edges):
        horizontal = edges & (Qt.LeftEdge | Qt.RightEdge)
        vertical = edges & (Qt.TopEdge | Qt.BottomEdge)
        if horizontal and vertical:
            top_left = (edges & Qt.TopEdge and edges & Qt.LeftEdge)
            bottom_right = (edges & Qt.BottomEdge and edges & Qt.RightEdge)
            return Qt.SizeFDiagCursor if (top_left or bottom_right) else Qt.SizeBDiagCursor
        if horizontal:
            return Qt.SizeHorCursor
        if vertical:
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def mousePressEvent(self, event):
        if self._frameless and event.button() == Qt.LeftButton:
            edges = self._edges_at(event.position().toPoint())
            if edges:
                handle = self.windowHandle()
                if handle:
                    handle.startSystemResize(edges)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def closeEvent(self, event):
        if not self._quitting and self._hide_to_tray():
            event.ignore()
            self.devices_page.deactivate()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.notify(
                    "Still running", "MiniDLNA is in the tray. Use its menu to quit."
                )
            return

        if not self._dirty:
            self._accept_close(event)
            return

        box = QMessageBox(self)
        box.setWindowTitle("Unsaved changes")
        box.setText("You have unsaved changes to your configuration.")
        box.setInformativeText("Apply them before closing?")
        box.setIcon(QMessageBox.Warning)
        apply_button = box.addButton("Apply and close", QMessageBox.AcceptRole)
        discard_button = box.addButton("Discard", QMessageBox.DestructiveRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(apply_button)
        box.exec()

        clicked = box.clickedButton()
        if clicked is discard_button:
            self._accept_close(event)
        elif clicked is apply_button:
            event.ignore()
            self._close_after_apply = True
            self._quitting = True
            self._apply(restart=True)
        else:
            event.ignore()
            self._quitting = False

    def _accept_close(self, event) -> None:
        # Qt is told not to quit on its own (the tray keeps the app alive),
        # so a real close has to end the application explicitly.
        event.accept()
        QTimer.singleShot(0, QApplication.quit)
