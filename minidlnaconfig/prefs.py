# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""The app's own preferences (as opposed to minidlna's configuration)."""

from __future__ import annotations

from PySide6.QtCore import QSettings

#: key -> default
DEFAULTS = {
    "tray_icon": True,
    "close_to_tray": False,
    "notifications": True,
    "auto_restart_on_drive": False,
}


class Preferences:
    def __init__(self, store: QSettings = None):
        self._store = store or QSettings("minidlna-config", "minidlna-config")

    def get(self, key: str) -> bool:
        value = self._store.value(key, DEFAULTS[key])
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)

    def set(self, key: str, value: bool) -> None:
        self._store.setValue(key, bool(value))
        self._store.sync()
