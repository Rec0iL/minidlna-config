# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Core data types for minidlna media directory configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Flag, auto


class MediaKind(Flag):
    """Media types minidlna can be told to index for a directory.

    minidlna encodes these as a prefix on ``media_dir``: ``A`` audio, ``V``
    video, ``P`` pictures.  They combine, so ``media_dir=PV,/path`` is legal
    and means "pictures and video".  A bare ``media_dir=/path`` means all.
    """

    AUDIO = auto()
    VIDEO = auto()
    PICTURES = auto()

    ALL = AUDIO | VIDEO | PICTURES

    @classmethod
    def parse(cls, letters: str) -> "MediaKind":
        """Parse a ``media_dir`` type prefix such as ``""``, ``"V"`` or ``"PV"``.

        Raises ValueError on an unknown letter so the caller can report the
        offending line instead of silently dropping the directory.
        """
        letters = letters.strip()
        if not letters:
            return cls.ALL

        kind = None
        for char in letters.upper():
            if char == "A":
                member = cls.AUDIO
            elif char == "V":
                member = cls.VIDEO
            elif char == "P":
                member = cls.PICTURES
            else:
                raise ValueError(f"unknown media type {char!r}")
            kind = member if kind is None else kind | member
        return kind

    @property
    def letters(self) -> str:
        """The config-file prefix, empty when every type is selected."""
        if self is MediaKind.ALL:
            return ""
        out = ""
        for char, name in (("A", "AUDIO"), ("P", "PICTURES"), ("V", "VIDEO")):
            if self & MediaKind[name]:
                out += char
        return out

    @property
    def label(self) -> str:
        """Human readable description, e.g. "Video" or "Pictures + Video"."""
        if self is MediaKind.ALL:
            return "All media"
        names = [
            name.capitalize()
            for name in ("AUDIO", "PICTURES", "VIDEO")
            if self & MediaKind[name]
        ]
        return " + ".join(names) if names else "Nothing"


@dataclass
class MediaFolder:
    """One ``media_dir`` entry."""

    kinds: MediaKind
    path: str

    @property
    def spec(self) -> str:
        """The value written after ``media_dir=``."""
        letters = self.kinds.letters
        return f"{letters},{self.path}" if letters else self.path

    @property
    def display_path(self) -> str:
        """Path with ``$HOME`` collapsed to ``~`` for display."""
        home = os.path.expanduser("~")
        if self.path == home:
            return "~"
        if self.path.startswith(home + os.sep):
            return "~" + self.path[len(home):]
        return self.path

    @property
    def name(self) -> str:
        """Trailing directory name, used as the row title."""
        return os.path.basename(self.path.rstrip(os.sep)) or self.path
