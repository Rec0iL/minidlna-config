# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Locating bundled assets, in both the source tree and an installed copy."""

from __future__ import annotations

import os
from typing import Optional

ICON_NAME = "minidlna-config"

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def icon_path() -> Optional[str]:
    """Absolute path to the application icon, or None if it is missing.

    ``assets/`` sits beside the package in both layouts: next to the source
    checkout, and next to the installed package under the data directory.
    """
    candidates = (
        os.path.join(os.path.dirname(_PACKAGE_DIR), "assets", f"{ICON_NAME}.svg"),
        os.path.join(_PACKAGE_DIR, "assets", f"{ICON_NAME}.svg"),
    )
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def app_icon():
    """A QIcon for the application, falling back to the icon theme."""
    from PySide6.QtGui import QIcon

    path = icon_path()
    if path:
        return QIcon(path)
    return QIcon.fromTheme(ICON_NAME)
