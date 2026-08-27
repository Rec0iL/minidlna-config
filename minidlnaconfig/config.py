# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Reading and writing minidlna.conf without disturbing the rest of the file."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .models import MediaFolder, MediaKind

#: Locations checked when no config path is supplied, in priority order.
SEARCH_PATHS = (
    "/etc/minidlna.conf",
    "/etc/minidlna/minidlna.conf",
    "/usr/local/etc/minidlna.conf",
    "~/.config/minidlna/minidlna.conf",
    "~/.minidlna/minidlna.conf",
    "~/minidlna.conf",
)

_MEDIA_DIR_RE = re.compile(r"^\s*media_dir\s*=\s*(?P<value>.*?)\s*$")
_SETTING_RE = re.compile(r"^\s*(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*?)\s*$")

#: Comment block written above the media_dir entries when the file has none yet.
_BLOCK_HEADER = "# Media directories (managed by minidlna-config)\n"


@dataclass
class ParseIssue:
    """A media_dir line that could not be understood."""

    line_number: int
    text: str
    reason: str


@dataclass
class MinidlnaConfig:
    """A parsed minidlna.conf.

    Every line of the original file is kept in :attr:`lines`.  Saving rewrites
    only the ``media_dir`` entries, in place, leaving comments, ordering and
    unrelated settings untouched.
    """

    path: Optional[str] = None
    lines: List[str] = field(default_factory=list)
    folders: List[MediaFolder] = field(default_factory=list)
    issues: List[ParseIssue] = field(default_factory=list)
    exists: bool = False

    # ---------------------------------------------------------------- loading

    @staticmethod
    def discover() -> Optional[str]:
        """Return the first config file that exists, or None."""
        env = os.environ.get("MINIDLNA_CONF")
        candidates = (env, *SEARCH_PATHS) if env else SEARCH_PATHS
        for candidate in candidates:
            expanded = os.path.expanduser(candidate)
            if os.path.isfile(expanded):
                return expanded
        return None

    @classmethod
    def load(cls, path: Optional[str] = None) -> "MinidlnaConfig":
        """Load a config file.  A missing file yields an empty, valid object."""
        if path is None:
            path = cls.discover()
        config = cls(path=path)
        if not path or not os.path.isfile(path):
            return config

        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            config.lines = handle.readlines()
        config.exists = True
        config._parse()
        return config

    def _parse(self) -> None:
        self.folders = []
        self.issues = []
        for number, line in enumerate(self.lines, 1):
            if line.lstrip().startswith("#"):
                continue
            match = _MEDIA_DIR_RE.match(line)
            if not match:
                continue
            value = match.group("value")
            if not value:
                self.issues.append(ParseIssue(number, line.rstrip("\n"), "empty media_dir"))
                continue
            # "V,/path" -> flags + path;  "/path" -> all media types.
            # Paths may themselves contain commas, so split only once.
            if "," in value:
                prefix, _, remainder = value.partition(",")
                try:
                    kinds = MediaKind.parse(prefix)
                except ValueError:
                    # Not a type prefix after all - treat the whole value as a
                    # path so a directory named "foo,bar" still round-trips.
                    self.folders.append(MediaFolder(MediaKind.ALL, value))
                    continue
                self.folders.append(MediaFolder(kinds, remainder.strip()))
            else:
                self.folders.append(MediaFolder(MediaKind.ALL, value))

    # ---------------------------------------------------------------- reading

    def setting(self, key: str) -> Optional[str]:
        """Return the value of an uncommented ``key=value`` setting."""
        for line in self.lines:
            if line.lstrip().startswith("#"):
                continue
            match = _SETTING_RE.match(line)
            if match and match.group("key") == key:
                return match.group("value")
        return None

    @property
    def port(self) -> int:
        raw = self.setting("port")
        try:
            return int(raw) if raw else 8200
        except ValueError:
            return 8200

    @property
    def friendly_name(self) -> str:
        return self.setting("friendly_name") or ""

    @property
    def db_dir(self) -> str:
        return self.setting("db_dir") or "/var/cache/minidlna"

    # ---------------------------------------------------------------- writing

    def render(self, folders: Optional[List[MediaFolder]] = None) -> str:
        """Return the full file content with media_dir entries replaced.

        The new block is placed where the first existing ``media_dir`` line
        was, so the surrounding explanatory comments still make sense.
        """
        if folders is None:
            folders = self.folders

        new_block = [f"media_dir={folder.spec}\n" for folder in folders]

        keep: List[str] = []
        insert_at: Optional[int] = None
        for line in self.lines:
            if not line.lstrip().startswith("#") and _MEDIA_DIR_RE.match(line):
                if insert_at is None:
                    insert_at = len(keep)
                continue
            keep.append(line)

        if insert_at is None:
            # No entries yet: append to the end, separated by a blank line.
            if keep and keep[-1].strip():
                keep.append("\n")
            elif not keep:
                keep.append(_BLOCK_HEADER)
            insert_at = len(keep)
            if keep and keep[-1] is not _BLOCK_HEADER:
                keep.append(_BLOCK_HEADER)
                insert_at = len(keep)

        result = keep[:insert_at] + new_block + keep[insert_at:]

        text = "".join(result)
        if text and not text.endswith("\n"):
            text += "\n"
        return text

    def needs_root(self) -> bool:
        """True when this process cannot write the config file itself."""
        if not self.path:
            return False
        target = self.path if os.path.exists(self.path) else os.path.dirname(self.path)
        return not os.access(target, os.W_OK)

    def write_directly(self, text: str, backup: bool = True) -> str:
        """Write ``text`` to :attr:`path` atomically, as the current user.

        Returns the backup path (empty string when no backup was made).  The
        replacement is atomic, so an interrupted write cannot leave a
        half-written config behind.
        """
        if not self.path:
            raise ValueError("no config path set")
        return atomic_write(self.path, text, backup=backup)


def atomic_write(dest: str, text: str, backup: bool = True) -> str:
    """Replace ``dest`` with ``text`` atomically, preserving mode and owner.

    Used both by the unprivileged path and by the root helper.
    """
    directory = os.path.dirname(os.path.abspath(dest)) or "."
    os.makedirs(directory, exist_ok=True)

    backup_path = ""
    if backup and os.path.exists(dest):
        backup_path = f"{dest}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(dest, backup_path)

    fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".minidlna-conf-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

        if os.path.exists(dest):
            info = os.stat(dest)
            os.chmod(temp_path, info.st_mode & 0o7777)
            try:
                os.chown(temp_path, info.st_uid, info.st_gid)
            except PermissionError:
                pass  # Not root and not the owner; keep our own ownership.
        else:
            os.chmod(temp_path, 0o644)

        os.replace(temp_path, dest)
    except BaseException:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise
    return backup_path
