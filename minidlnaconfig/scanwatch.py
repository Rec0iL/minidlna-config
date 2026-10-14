# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Keeping the window in step with a running library scan.

A rebuild returns as soon as the service has been started, long before minidlna
has finished indexing. Reading the database once at that point shows an almost
empty index, and nothing ever corrected it. This watcher follows the scan
instead: it polls while one is running, reports progress, and says when it is
over so that the views can be redrawn with the finished result.
"""

from __future__ import annotations

import time
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from . import library, scan
from .models import MediaKind
from .service import ServiceController

IDLE_INTERVAL_MS = 5000
ACTIVE_INTERVAL_MS = 1000

#: After a restart or rebuild, poll quickly for this long waiting for the
#: scanner to appear.
WATCH_WINDOW_S = 20.0

#: What the window tells the watcher on every tick: the index to read, the
#: folder paths to report counts for, and the configured folders (with their
#: media types) that a scan will actually walk.
Source = Tuple[str, List[str], List[Tuple[str, MediaKind]]]


class _Signals(QObject):
    polled = Signal(object)
    counted = Signal(int, object)


class _Job(QRunnable):
    def __init__(self, function: Callable[[], object], signal, *args):
        super().__init__()
        self._function = function
        self._signal = signal
        self._args = args

    def run(self) -> None:
        try:
            result = self._function()
        except Exception:                              # pragma: no cover - defensive
            result = None
        self._signal.emit(*self._args, result)


class ScanWatcher(QObject):
    """Emits progress while minidlna scans and a final result when it stops."""

    #: (ScanState, LibraryStats) on every poll during a scan.
    progress = Signal(object, object)
    #: (LibraryStats, announce) when a scan has ended, or a requested refresh
    #: has settled. ``announce`` is true when a scan was actually observed.
    finished = Signal(object, bool)

    def __init__(self, service: ServiceController, source: Callable[[], Source], parent=None):
        super().__init__(parent)
        self._service = service
        self._source = source
        self._pool = QThreadPool.globalInstance()
        self._signals = _Signals(self)
        self._signals.polled.connect(self._on_polled)
        self._signals.counted.connect(self._on_counted)

        self._polling = False
        self._scanning = False
        self._expected: Optional[int] = None
        self._counting = False
        self._generation = 0
        self._watch_until = 0.0
        self._refresh_pending = False

        self._timer = QTimer(self)
        self._timer.setInterval(IDLE_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------- control

    def start(self) -> None:
        self._timer.start()
        self._tick()

    def kick(self) -> None:
        """Something that starts a scan just happened; follow it closely."""
        self._watch_until = time.monotonic() + WATCH_WINDOW_S
        self._refresh_pending = True
        # A rebuild replaces the database, so a count made against the old
        # scan no longer applies.
        self._generation += 1
        self._expected = None
        self._counting = False
        self._timer.setInterval(ACTIVE_INTERVAL_MS)
        self._tick()

    # ---------------------------------------------------------------- polling

    def _watching(self) -> bool:
        return time.monotonic() < self._watch_until

    def _tick(self) -> None:
        if self._polling:
            return
        self._polling = True

        db_path, row_paths, scanned = self._source()
        paths = list(dict.fromkeys(row_paths + [path for path, _ in scanned]))
        scanned_paths = [path for path, _ in scanned]
        # Read the index while a scan runs, once it ends, and when a refresh
        # that never saw a scan has had long enough to settle.
        settled_without_scan = self._refresh_pending and not self._watching()
        read = self._scanning or settled_without_scan

        def work():
            status = self._service.status()
            scanning = status.active and bool(scan.scanner_pids(status.pid))
            stats = None
            if scanning or read or self._scanning:
                stats = library.read_stats(db_path, paths)
            return scanning, stats, scanned_paths

        self._pool.start(_Job(work, self._signals.polled))

    def _on_polled(self, result) -> None:
        self._polling = False
        if result is None:
            return
        scanning, stats, scanned_paths = result
        was_scanning = self._scanning
        self._scanning = scanning

        if scanning and stats is not None:
            self._refresh_pending = False
            self._start_counting()
            indexed = 0
            if stats.available:
                by_path = {folder.path: folder for folder in stats.folders}
                indexed = sum(
                    by_path[path].files for path in scanned_paths if path in by_path
                )
            state = scan.ScanState(True, indexed, self._expected)
            self._timer.setInterval(ACTIVE_INTERVAL_MS)
            self.progress.emit(state, stats)
            return

        if scanning:
            # The first poll to notice a scan has no stats yet; the next one
            # reads them.
            self._timer.setInterval(ACTIVE_INTERVAL_MS)
            return

        if was_scanning and stats is not None:
            self._generation += 1
            self._expected = None
            self._counting = False
            self._refresh_pending = False
            self.finished.emit(stats, True)
        elif self._refresh_pending and stats is not None and not self._watching():
            self._refresh_pending = False
            self.finished.emit(stats, False)

        keep_fast = self._refresh_pending or self._watching()
        self._timer.setInterval(ACTIVE_INTERVAL_MS if keep_fast else IDLE_INTERVAL_MS)

    # ------------------------------------------------------------- estimate

    def _start_counting(self) -> None:
        if self._counting or self._expected is not None:
            return
        _, _, scanned = self._source()
        generation = self._generation
        self._counting = True

        def work():
            return scan.count_media_files(
                scanned, should_stop=lambda: generation != self._generation
            )

        self._pool.start(_Job(work, self._signals.counted, generation))

    def _on_counted(self, generation: int, total) -> None:
        if generation != self._generation:
            return
        self._counting = False
        if total is not None:
            self._expected = total
