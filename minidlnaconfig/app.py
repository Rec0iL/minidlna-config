# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Application entry point."""

from __future__ import annotations

import argparse
import os
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="minidlna-config",
        description="Configure the media folders served by minidlna.",
    )
    parser.add_argument(
        "-c", "--config", metavar="PATH",
        help="minidlna.conf to edit (default: autodetected)",
    )
    args = parser.parse_args(argv)

    if os.geteuid() == 0 and not os.environ.get("MINIDLNA_GUI_ALLOW_ROOT"):
        # The old launcher ran the whole GUI under pkexec.  That gives every
        # part of a graphical toolkit root privileges for the sake of one file
        # write, so refuse by default and elevate per-action instead.
        sys.stderr.write(
            "minidlna-config does not need to run as root.\n"
            "Start it as your normal user; it asks for authorisation only when "
            "it actually writes the configuration.\n"
        )
        return 1

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from . import resources, theme
    from .window import MainWindow

    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("MiniDLNA Configuration")
    app.setApplicationDisplayName("MiniDLNA Configuration")
    app.setDesktopFileName("minidlna-config")
    app.setWindowIcon(resources.app_icon())
    # Palette first: standard dialogs are drawn from it, not the stylesheet.
    theme.apply_palette(app)
    app.setStyleSheet(theme.QSS)

    font = QFont(app.font())
    font.setPointSizeF(10.0)
    app.setFont(font)

    window = MainWindow(args.config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
