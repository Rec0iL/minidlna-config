#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Render the SVG logo to PNGs at the standard icon-theme sizes.

The PNGs are committed alongside the SVG so that installing needs no rendering
toolchain, and so icon loaders that do not handle scalable-only themes still
find an icon. Re-run this after editing minidlna-config.svg:

    python3 assets/render-icons.py
"""

import os
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

SIZES = (16, 22, 24, 32, 48, 64, 128, 256)
HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "minidlna-config"


def main() -> int:
    QApplication(sys.argv[:1])
    source = os.path.join(HERE, f"{NAME}.svg")
    renderer = QSvgRenderer(source)
    if not renderer.isValid():
        print(f"could not read {source}", file=sys.stderr)
        return 1

    for size in SIZES:
        target_dir = os.path.join(HERE, "icons", f"{size}x{size}", "apps")
        os.makedirs(target_dir, exist_ok=True)
        image = QImage(size, size, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        target = os.path.join(target_dir, f"{NAME}.png")
        image.save(target)
        print(f"  {size:>3}px  {os.path.relpath(target, HERE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
