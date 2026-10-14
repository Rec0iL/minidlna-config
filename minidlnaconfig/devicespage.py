# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""The Devices view: who is connected, and what is being streamed."""

from __future__ import annotations

import time
from typing import Dict, List, Tuple

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import devices, journal, library, theme
from .tasks import TaskError, run_async

#: How often the live view is refreshed while it is on screen.
POLL_MS = 3000
#: The log is a heavier read, so the discovery history is refreshed less often.
SSDP_EVERY_S = 30.0


def _seen(stamp: float) -> str:
    if not stamp:
        return ""
    seconds = max(0, int(time.time() - stamp))
    if seconds < 90:
        return "just now"
    for limit, divisor, noun in ((3600, 60, "minute"), (86400, 3600, "hour"), (86400 * 30, 86400, "day")):
        if seconds < limit:
            value = seconds // divisor
            return f"{value} {noun}{'' if value == 1 else 's'} ago"
    return time.strftime("%Y-%m-%d", time.localtime(stamp))


class DevicesPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._port = 8200
        self._unit = "minidlna.service"
        self._log_dir = ""
        self._active = False
        self._busy = False

        self._ssdp: Dict[str, Tuple[int, float]] = {}
        self._ssdp_read_at = 0.0
        #: ip -> (bytes_sent, monotonic time) from the previous poll
        self._previous: Dict[str, Tuple[int, float]] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        header = QHBoxLayout()
        self.summary = QLabel("")
        self.summary.setObjectName("subtitle")
        self.summary.setWordWrap(True)
        header.addWidget(self.summary, 1)
        refresh = QPushButton("Refresh", self)
        refresh.setCursor(QCursor(Qt.PointingHandCursor))
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        outer.addLayout(header)

        self.table = QTreeWidget(self)
        self.table.setObjectName("libraryTable")
        self.table.setColumnCount(5)
        self.table.setHeaderLabels(["Device", "Address", "Hardware", "Activity", "Sent"])
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header_view = self.table.header()
        header_view.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3, 4):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        outer.addWidget(self.table, 1)

        self.hint = QLabel(
            "Devices appear here once they have asked minidlna for something or "
            "announced themselves on the network."
        )
        self.hint.setObjectName("emptyHint")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self.refresh)

    # ----------------------------------------------------------------- state

    def set_context(self, port: int, unit: str, log_dir: str) -> None:
        self._port, self._unit, self._log_dir = port, unit, log_dir

    def activate(self) -> None:
        self._active = True
        self.refresh()
        self._timer.start()

    def deactivate(self) -> None:
        self._active = False
        self._timer.stop()

    # ------------------------------------------------------------------ data

    def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        port, unit, log_dir = self._port, self._unit, self._log_dir
        read_log = time.monotonic() - self._ssdp_read_at > SSDP_EVERY_S

        def gather():
            status = devices.fetch_status_page(port)
            connections = devices.open_connections(port)
            macs = devices.neighbours()
            ssdp = None
            if read_log:
                entries, _ = journal.read_journal(unit, since="-1d", limit=5000, log_dir=log_dir)
                ssdp = journal.ssdp_senders(entries)
            return status, connections, macs, ssdp

        run_async(gather, self._on_gathered, self)

    def _on_gathered(self, result) -> None:
        self._busy = False
        if isinstance(result, TaskError):
            self.summary.setText(f"Could not read device information: {result}")
            return
        status, connections, macs, ssdp = result
        if ssdp is not None:
            self._ssdp = ssdp
            self._ssdp_read_at = time.monotonic()

        found = devices.merge(status, connections, self._ssdp, macs)

        now = time.monotonic()
        rates: Dict[str, float] = {}
        current: Dict[str, Tuple[int, float]] = {}
        for device in found:
            current[device.ip] = (device.bytes_sent, now)
            before = self._previous.get(device.ip)
            if before and device.bytes_sent >= before[0] and now > before[1]:
                rates[device.ip] = (device.bytes_sent - before[0]) / (now - before[1])
        self._previous = current

        self._render(found, rates, status)

    # ---------------------------------------------------------------- render

    def _render(self, found: List[devices.Device], rates: Dict[str, float], status) -> None:
        if not status.reachable:
            self.summary.setText(
                "minidlna is not answering, so live activity is unavailable. "
                "Devices seen earlier are still listed from the log."
            )
        else:
            streaming = sum(1 for d in found if d.streaming)
            total = status.counts
            library_text = ""
            if total:
                library_text = "  ·  library: " + ", ".join(
                    f"{n:,} {name.replace(' files', '').lower()}" for name, n in total.items() if n
                )
            noun = "device" if len(found) == 1 else "devices"
            self.summary.setText(f"{len(found)} {noun}  ·  {streaming} connected now{library_text}")

        self.table.setUpdatesEnabled(False)
        self.table.clear()
        rows = []
        for device in found:
            rate = rates.get(device.ip, 0.0)
            if device.streaming and rate > 1000:
                activity = f"Streaming · {devices.format_rate(rate)}"
                colour = theme.GREEN
            elif device.streaming:
                activity = "Connected"
                colour = theme.GREEN
            elif device.last_seen:
                activity = f"Seen {_seen(device.last_seen)}"
                colour = theme.TEXT_DIM
            else:
                activity = "Idle"
                colour = theme.TEXT_DIM

            name = device.kind or "Unknown device"
            row = QTreeWidgetItem([
                name,
                device.ip,
                device.mac,
                activity,
                library.format_size(device.bytes_sent) if device.bytes_sent else "",
            ])
            row.setForeground(3, QColor(colour))
            row.setTextAlignment(4, Qt.AlignRight | Qt.AlignVCenter)
            if device.announced:
                row.setToolTip(0, f"Announced itself {device.announced} times in the last day")
            rows.append(row)
        self.table.addTopLevelItems(rows)
        self.table.setUpdatesEnabled(True)
        self.hint.setVisible(not rows)
