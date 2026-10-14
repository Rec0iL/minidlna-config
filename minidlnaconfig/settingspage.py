# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""The Settings view: server options and the app's own preferences."""

from __future__ import annotations

from typing import Dict, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor, QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import settings as schema
from . import theme
from .prefs import Preferences
from .widgets import GlassPanel, Toggle

#: (preference key, title, help)
APP_PREFERENCES = (
    ("tray_icon", "Show a tray icon",
     "Status at a glance, and quick restart and rebuild actions."),
    ("close_to_tray", "Keep running in the tray when the window is closed",
     "Closing the window hides it instead of quitting. Quit from the tray menu."),
    ("notifications", "Desktop notifications",
     "Tell me when a scan finishes, a drive goes missing, or minidlna stops."),
    ("auto_restart_on_drive", "Rebuild automatically when a missing drive returns",
     "minidlna skips folders that are not mounted at start-up. This rebuilds the "
     "library once they appear. It asks for authentication each time."),
)


class _Row(QWidget):
    """A title and help text on the left, a control on the right."""

    def __init__(self, title: str, help_text: str, control: QWidget, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(18)

        text = QVBoxLayout()
        text.setSpacing(2)
        name = QLabel(title)
        name.setStyleSheet("font-weight: 600;")
        text.addWidget(name)
        if help_text:
            note = QLabel(help_text)
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {theme.TEXT_DIM}; font-size: 12px;")
            text.addWidget(note)
        layout.addLayout(text, 1)
        layout.addWidget(control, 0, Qt.AlignVCenter | Qt.AlignRight)


class SettingsPage(QWidget):
    #: A minidlna.conf value was edited.
    changed = Signal()
    #: (key, value) for one of the app's own preferences.
    preferenceChanged = Signal(str, bool)
    backupsRequested = Signal()

    def __init__(self, preferences: Preferences, parent=None):
        super().__init__(parent)
        self._preferences = preferences
        self._controls: Dict[str, QWidget] = {}
        self._original: Dict[str, str] = {}
        self._loading = False

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        container = QWidget()
        container.setAttribute(Qt.WA_TranslucentBackground, True)
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 6, 0)
        column.setSpacing(12)
        scroll.setWidget(container)

        for group in schema.GROUPS:
            specs = [spec for spec in schema.SPECS if spec.group == group]
            panel, layout = self._panel(group)
            for spec in specs:
                control = self._make_control(spec)
                self._controls[spec.key] = control
                layout.addWidget(_Row(spec.label, spec.help, control, panel))
            column.addWidget(panel)

        panel, layout = self._panel("This app")
        for key, title, help_text in APP_PREFERENCES:
            toggle = Toggle(panel)
            toggle.setChecked(preferences.get(key))
            toggle.toggled.connect(lambda checked, k=key: self._on_preference(k, checked))
            layout.addWidget(_Row(title, help_text, toggle, panel))
        column.addWidget(panel)

        panel, layout = self._panel("Backups")
        self.backup_button = QPushButton("Restore a previous version…")
        self.backup_button.setCursor(QCursor(Qt.PointingHandCursor))
        self.backup_button.clicked.connect(self.backupsRequested.emit)
        layout.addWidget(_Row(
            "Configuration history",
            "Every save keeps a copy of the previous file. Compare it with the "
            "current one and roll back if a change went wrong.",
            self.backup_button, panel,
        ))
        column.addWidget(panel)
        column.addStretch(1)

    # ----------------------------------------------------------------- build

    def _panel(self, title: str):
        panel = GlassPanel(self, radius=14, fill=0.04, stroke=0.09)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(0)
        heading = QLabel(title.upper())
        heading.setObjectName("sectionLabel")
        layout.addWidget(heading)
        return panel, layout

    def _make_control(self, spec: schema.SettingSpec) -> QWidget:
        if spec.kind == "bool":
            toggle = Toggle(self)
            toggle.toggled.connect(self._on_edited)
            return toggle
        if spec.kind == "choice":
            combo = QComboBox(self)
            combo.setMinimumWidth(240)
            for value, label in spec.choices:
                combo.addItem(label, value)
            combo.currentIndexChanged.connect(self._on_edited)
            return combo
        edit = QLineEdit(self)
        edit.setMinimumWidth(280 if spec.kind == "text" else 120)
        edit.setPlaceholderText(spec.default or "default")
        if spec.kind == "int":
            edit.setValidator(QIntValidator(1, 86400 if spec.key == "notify_interval" else 65535, edit))
        edit.textEdited.connect(self._on_edited)
        return edit

    # ---------------------------------------------------------------- values

    def load(self, raw: Dict[str, str]) -> None:
        """Show the file's values."""
        self._loading = True
        self._original = dict(raw)
        for spec in schema.SPECS:
            value = raw.get(spec.key, "")
            control = self._controls[spec.key]
            shown = schema.normalise(spec, schema.effective(spec, value))
            if spec.kind == "bool":
                control.setChecked(shown == "yes")
            elif spec.kind == "choice":
                index = control.findData(shown)
                control.setCurrentIndex(max(0, index))
            else:
                control.setText(value)
        self._loading = False

    def values(self) -> Dict[str, str]:
        """Desired raw values; unchanged ones are returned exactly as loaded."""
        result: Dict[str, str] = {}
        for spec in schema.SPECS:
            original = self._original.get(spec.key, "")
            control = self._controls[spec.key]
            if spec.kind == "bool":
                shown = "yes" if control.isChecked() else "no"
            elif spec.kind == "choice":
                shown = control.currentData() or ""
            else:
                result[spec.key] = control.text().strip()
                continue
            unchanged = shown == schema.normalise(spec, schema.effective(spec, original))
            result[spec.key] = original if unchanged else shown
        return result

    def warnings_for(self, before: Dict[str, str], after: Dict[str, str]) -> List[str]:
        """Warnings for risky settings that differ between two value sets."""
        notes = []
        for spec in schema.SPECS:
            if spec.warn and before.get(spec.key, "") != after.get(spec.key, ""):
                notes.append(f"{spec.label}: {spec.warn}")
        return notes

    def set_backup_count(self, count: int) -> None:
        self.backup_button.setEnabled(count > 0)
        self.backup_button.setText(
            "Restore a previous version…" if count else "No backups yet"
        )

    # --------------------------------------------------------------- signals

    def _on_edited(self, *_args) -> None:
        if not self._loading:
            self.changed.emit()

    def _on_preference(self, key: str, checked: bool) -> None:
        self._preferences.set(key, checked)
        self.preferenceChanged.emit(key, checked)
