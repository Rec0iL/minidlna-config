# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Colour palette and stylesheet for the application."""

from __future__ import annotations

# Deliberately a single, committed dark look - the aurora background is the
# identity of the app and a light variant would fight it.
BG_DEEP = "#05070F"
BG_BASE = "#080B18"

TEXT = "#E8EEFF"
TEXT_DIM = "rgba(232, 238, 255, 0.62)"
TEXT_FAINT = "rgba(232, 238, 255, 0.38)"

GLASS = "rgba(255, 255, 255, 0.05)"
GLASS_STRONG = "rgba(255, 255, 255, 0.085)"
STROKE = "rgba(255, 255, 255, 0.11)"
STROKE_SOFT = "rgba(255, 255, 255, 0.07)"

VIOLET = "#8B5CFF"
INDIGO = "#5B8DEF"
CYAN = "#22D3EE"
AMBER = "#FFB86B"
PINK = "#FF6B9D"
GREEN = "#42E39B"
RED = "#FF5C7A"

#: Per-media-type accent colours, keyed by the config-file letter.
KIND_COLORS = {
    "A": AMBER,
    "P": CYAN,
    "V": VIOLET,
}

QSS = f"""
QWidget {{
    color: {TEXT};
    font-family: "Inter", "Cantarell", "Noto Sans", "DejaVu Sans", sans-serif;
    font-size: 14px;
}}

QToolTip {{
    background: #141A2E;
    color: {TEXT};
    border: 1px solid {STROKE};
    border-radius: 8px;
    padding: 6px 10px;
}}

/* ---------------------------------------------------------------- buttons */

QPushButton {{
    background: {GLASS};
    border: 1px solid {STROKE};
    border-radius: 10px;
    padding: 9px 16px;
    color: {TEXT};
    font-weight: 500;
}}
QPushButton:hover  {{ background: {GLASS_STRONG}; border-color: rgba(255,255,255,0.20); }}
QPushButton:pressed{{ background: rgba(255,255,255,0.03); }}
QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {STROKE_SOFT}; background: transparent; }}

QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {VIOLET}, stop:1 {INDIGO});
    border: none;
    color: white;
    font-weight: 600;
    padding: 10px 22px;
}}
QPushButton#primary:hover  {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #9E75FF, stop:1 #6F9DF5);
}}
QPushButton#primary:disabled {{
    background: rgba(255,255,255,0.06);
    color: {TEXT_FAINT};
}}

QPushButton#iconGhost, QPushButton#iconDanger {{
    background: transparent;
    border: none;
    border-radius: 9px;
    padding: 6px;
    color: {TEXT_DIM};
    font-size: 15px;
}}
QPushButton#iconGhost:hover {{ background: rgba(255,255,255,0.10); color: {TEXT}; }}
QPushButton#iconGhost:disabled, QPushButton#iconDanger:disabled {{
    color: {TEXT_FAINT}; background: transparent;
}}
QPushButton#iconDanger:hover {{ background: rgba(255,92,122,0.18); color: {RED}; }}

QPushButton#windowClose:hover {{ background: {RED}; color: white; }}

/* ------------------------------------------------------------------ input */

QLineEdit {{
    background: rgba(0, 0, 0, 0.28);
    border: 1px solid {STROKE};
    border-radius: 9px;
    padding: 8px 12px;
    selection-background-color: {VIOLET};
}}
QLineEdit:focus {{ border-color: {VIOLET}; background: rgba(0,0,0,0.38); }}
QLineEdit#pathEdit {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 3px 7px;
    color: {TEXT_DIM};
    font-size: 12.5px;
}}
QLineEdit#pathEdit:hover  {{ border-color: {STROKE_SOFT}; background: rgba(0,0,0,0.20); }}
QLineEdit#pathEdit:focus  {{ border-color: {VIOLET}; background: rgba(0,0,0,0.34); color: {TEXT}; }}

/* ----------------------------------------------------------------- labels */

QLabel#title      {{ font-size: 19px; font-weight: 600; letter-spacing: 0.2px; }}
QLabel#subtitle   {{ color: {TEXT_DIM}; font-size: 13px; }}
QLabel#sectionLabel {{
    color: {TEXT_FAINT};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.3px;
}}
QLabel#folderName {{ font-size: 15px; font-weight: 600; }}
QLabel#emptyTitle {{ font-size: 17px; font-weight: 600; color: {TEXT_DIM}; }}
QLabel#emptyHint  {{ color: {TEXT_FAINT}; font-size: 13px; }}

/* ------------------------------------------------------------- scrollbars */

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: rgba(255,255,255,0.14);
    border-radius: 5px;
    min-height: 40px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.26); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0px; }}

/* ------------------------------------------------------------------ menus */

QMenu {{
    background: #10162A;
    border: 1px solid {STROKE};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{ padding: 7px 18px; border-radius: 7px; }}
QMenu::item:selected {{ background: {GLASS_STRONG}; }}
QMenu::separator {{ height: 1px; background: {STROKE_SOFT}; margin: 5px 8px; }}
"""


#: Styling for file dialogs.
#:
#: The application stylesheet sets a light text colour on every QWidget, which
#: would otherwise land on a file dialog's default light background and make it
#: unreadable. Rather than narrow the global rule, dialogs get a matching dark
#: treatment of their own.
DIALOG_QSS = f"""
QFileDialog {{ background: #0C1120; }}
QFileDialog QWidget {{ color: {TEXT}; }}
QFileDialog QLabel {{ color: {TEXT_DIM}; }}

QFileDialog QListView, QFileDialog QTreeView {{
    background: rgba(0, 0, 0, 0.30);
    border: 1px solid {STROKE};
    border-radius: 10px;
    outline: none;
    show-decoration-selected: 1;
}}
QFileDialog QListView::item, QFileDialog QTreeView::item {{
    padding: 4px;
    border-radius: 6px;
}}
QFileDialog QListView::item:hover, QFileDialog QTreeView::item:hover {{
    background: rgba(255, 255, 255, 0.07);
}}
QFileDialog QListView::item:selected, QFileDialog QTreeView::item:selected {{
    background: {VIOLET};
    color: white;
}}

QFileDialog QHeaderView::section {{
    background: rgba(255, 255, 255, 0.04);
    color: {TEXT_FAINT};
    border: none;
    border-bottom: 1px solid {STROKE_SOFT};
    padding: 6px 8px;
    font-weight: 600;
}}

QFileDialog QLineEdit, QFileDialog QComboBox {{
    background: rgba(0, 0, 0, 0.32);
    border: 1px solid {STROKE};
    border-radius: 9px;
    padding: 7px 10px;
    color: {TEXT};
    selection-background-color: {VIOLET};
}}
QFileDialog QLineEdit:focus, QFileDialog QComboBox:focus {{ border-color: {VIOLET}; }}
QFileDialog QComboBox::drop-down {{ border: none; width: 22px; }}
QFileDialog QComboBox QAbstractItemView {{
    background: #10162A;
    border: 1px solid {STROKE};
    border-radius: 8px;
    selection-background-color: {VIOLET};
}}

QFileDialog QToolButton {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 6px;
}}
QFileDialog QToolButton:hover {{ background: rgba(255, 255, 255, 0.10); }}
QFileDialog QToolButton:pressed {{ background: rgba(255, 255, 255, 0.04); }}

QFileDialog QPushButton {{
    background: {GLASS};
    border: 1px solid {STROKE};
    border-radius: 9px;
    padding: 8px 20px;
    min-width: 84px;
}}
QFileDialog QPushButton:hover {{ background: {GLASS_STRONG}; }}
QFileDialog QPushButton:default {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {VIOLET}, stop:1 {INDIGO});
    border: none;
    color: white;
    font-weight: 600;
}}
"""


def apply_palette(app) -> None:
    """Give the application an explicit dark palette.

    Standard dialogs - file choosers, message boxes - are drawn from the
    palette, not from this app's stylesheet. Without this they inherit the
    desktop's colours while the stylesheet forces light text onto them, which
    on a light desktop theme leaves them unreadable. Setting the palette makes
    the app look the same everywhere, which is the intent of the dark design.
    """
    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    window = QColor("#0C1120")
    base = QColor("#0A0E1C")
    text = QColor(TEXT)
    faint = QColor(232, 238, 255, 96)

    palette.setColor(QPalette.Window, window)
    palette.setColor(QPalette.WindowText, text)
    palette.setColor(QPalette.Base, base)
    palette.setColor(QPalette.AlternateBase, QColor("#101728"))
    palette.setColor(QPalette.Text, text)
    palette.setColor(QPalette.PlaceholderText, faint)
    palette.setColor(QPalette.Button, QColor("#141A2E"))
    palette.setColor(QPalette.ButtonText, text)
    palette.setColor(QPalette.BrightText, QColor("#FFFFFF"))
    palette.setColor(QPalette.ToolTipBase, QColor("#141A2E"))
    palette.setColor(QPalette.ToolTipText, text)
    palette.setColor(QPalette.Highlight, QColor(VIOLET))
    palette.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(QPalette.Link, QColor(CYAN))
    palette.setColor(QPalette.LinkVisited, QColor(PINK))

    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, faint)

    app.setPalette(palette)
