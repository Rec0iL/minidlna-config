# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Noticing drives coming and going while minidlna runs.

minidlna decides which media folders exist when it starts. A drive that is
mounted a little later is skipped until the next restart, and nothing says so.
This watches for exactly that: folders the log reports as inaccessible at
start-up that are present now, and configured folders that disappear.
"""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Set

from PySide6.QtCore import QObject, QTimer, Signal

from . import journal
from .service import ServiceController
from .tasks import TaskError, run_async

POLL_MS = 5000


class DriveWatcher(QObject):
    #: Folders minidlna skipped at start-up that are available now.
    appeared = Signal(list)
    #: Configured folders that existed a moment ago and are gone.
    vanished = Signal(list)
    #: Configured folders that came back (not necessarily skipped at start).
    returned = Signal(list)

    def __init__(
        self,
        service: ServiceController,
        configured: Callable[[], List[str]],
        unit: Callable[[], str],
        parent=None,
    ):
        super().__init__(parent)
        self._service = service
        self._configured = configured
        self._unit = unit

        self._busy = False
        self._run = None                     # ActiveEnterTimestamp of the run we know about
        self._skipped: List[str] = []
        self._announced: Set[str] = set()
        self._existed: Dict[str, bool] = {}

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()
        self._tick()

    def forget(self) -> None:
        """The folder list changed; compare against a fresh baseline."""
        self._existed = {}

    # ---------------------------------------------------------------- polling

    def _tick(self) -> None:
        if self._busy:
            return
        self._busy = True
        paths = list(self._configured())
        known_run = self._run
        unit = self._unit()

        def work():
            status = self._service.status()
            skipped = None
            if status.active and status.since and status.since != known_run:
                entries, _ = journal.read_journal(unit, since=status.since, limit=3000)
                skipped = journal.skipped_directories(entries)
            existing = {path: os.path.isdir(path) for path in paths}
            return status, skipped, existing

        run_async(work, self._on_polled, self)

    def _on_polled(self, result) -> None:
        self._busy = False
        if isinstance(result, TaskError):
            return
        status, skipped, existing = result

        if skipped is not None:
            self._run = status.since
            self._skipped = skipped
            self._announced = set()

        previous = self._existed
        self._existed = existing
        if previous:
            gone = [p for p, there in existing.items() if not there and previous.get(p)]
            back = [p for p, there in existing.items() if there and previous.get(p) is False]
            if gone:
                self.vanished.emit(gone)
            if back:
                self.returned.emit(back)

        if status.active:
            configured = set(existing)
            ready = [
                p for p in self._skipped
                if p in configured and existing.get(p) and p not in self._announced
            ]
            if ready:
                self._announced.update(ready)
                self.appeared.emit(ready)
