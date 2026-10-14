# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Running blocking work off the UI thread and getting the result back."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class TaskError:
    """Delivered to the callback instead of a result when the work raised."""

    def __init__(self, error: BaseException):
        self.error = error

    def __str__(self) -> str:
        return str(self.error)


class _Relay(QObject):
    done = Signal(object)


class _Work(QRunnable):
    def __init__(self, function: Callable[[], Any], relay: _Relay):
        super().__init__()
        self._function = function
        self._relay = relay

    def run(self) -> None:
        try:
            result = self._function()
        except BaseException as exc:                   # noqa: BLE001
            result = TaskError(exc)
        self._relay.done.emit(result)


def run_async(function: Callable[[], Any], callback: Callable[[Any], None], owner: QObject) -> None:
    """Call ``function`` on a worker thread, then ``callback(result)`` on the
    UI thread.

    The relay is owned by ``owner`` rather than by the runnable, because the
    pool deletes a runnable as soon as it finishes and a queued signal whose
    sender is gone is silently dropped. If ``owner`` is destroyed first the
    callback is simply never called.
    """
    relay = _Relay(owner)

    def deliver(result):
        relay.deleteLater()
        callback(result)

    relay.done.connect(deliver)
    QThreadPool.globalInstance().start(_Work(function, relay))
