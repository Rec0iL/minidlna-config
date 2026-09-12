# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 rec0il
"""Reading minidlna's media index.

minidlna keeps everything it has scanned in a SQLite database (``files.db``
inside its cache directory). Reading it is the only way to see what the server
actually found, as opposed to what it was told to look at - the two differ
whenever a directory is unreadable, empty, or holds nothing minidlna
recognises.

The database is opened read-only and never written to. Everything here is
standard library only, so it stays testable without a display server.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

#: Media kinds, in display order, keyed by the MIME prefix minidlna stores.
KINDS = ("video", "audio", "image")

#: How long to wait if minidlnad happens to be writing when we read.
TIMEOUT = 3.0


@dataclass
class FolderStat:
    """What was indexed underneath one configured media directory."""

    path: str
    files: int = 0
    size: int = 0
    kinds: dict = field(default_factory=dict)


@dataclass
class MediaItem:
    """One indexed file."""

    title: str
    path: str
    size: int
    duration: str
    resolution: str
    mime: str
    timestamp: int

    @property
    def kind(self) -> str:
        return self.mime.split("/", 1)[0] if self.mime else ""

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path)


@dataclass
class LibraryStats:
    """A summary of the whole index."""

    db_path: str = ""
    available: bool = False
    error: str = ""
    updated: float = 0.0
    files: int = 0
    size: int = 0
    kinds: dict = field(default_factory=dict)
    folders: List[FolderStat] = field(default_factory=list)
    unmatched: int = 0

    @property
    def is_empty(self) -> bool:
        return self.available and self.files == 0


def database_path(db_dir: str) -> str:
    """Where minidlna keeps its index for the given cache directory."""
    return os.path.join(db_dir or "/var/cache/minidlna", "files.db")


def _connect(db_path: str) -> sqlite3.Connection:
    """Open the index read-only.

    A read-only URI means a bug here can never damage the server's database,
    and lets us read it while minidlnad is running.
    """
    uri = "file:" + db_path.replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=TIMEOUT)


def _like(text: str) -> str:
    """Escape a user's search text for use inside a LIKE pattern."""
    for char in ("\\", "%", "_"):
        text = text.replace(char, "\\" + char)
    return f"%{text}%"


def read_stats(db_path: str, folders: Optional[List[str]] = None) -> LibraryStats:
    """Summarise the index: totals, per media type, per configured folder.

    Never raises; a missing or unreadable database is reported through the
    returned object so the UI can explain it.
    """
    stats = LibraryStats(db_path=db_path)

    if not os.path.isfile(db_path):
        stats.error = "no media database yet"
        return stats

    try:
        stats.updated = os.path.getmtime(db_path)
    except OSError:
        pass

    try:
        with _connect(db_path) as con:
            # Rows without a MIME type are the folder entries minidlna keeps
            # alongside the files; only the files are media.
            row = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(SIZE), 0) FROM DETAILS WHERE MIME IS NOT NULL"
            ).fetchone()
            stats.files, stats.size = row[0], row[1]

            for kind in KINDS:
                count, size = con.execute(
                    "SELECT COUNT(*), COALESCE(SUM(SIZE), 0) "
                    "FROM DETAILS WHERE MIME LIKE ?",
                    (kind + "/%",),
                ).fetchone()
                stats.kinds[kind] = (count, size)

            for path in folders or []:
                base = path.rstrip("/")
                stat = FolderStat(path=path)
                stat.files, stat.size = con.execute(
                    "SELECT COUNT(*), COALESCE(SUM(SIZE), 0) FROM DETAILS "
                    "WHERE MIME IS NOT NULL AND PATH LIKE ? ESCAPE '\\'",
                    (_like_prefix(base),),
                ).fetchone()
                for kind in KINDS:
                    count, size = con.execute(
                        "SELECT COUNT(*), COALESCE(SUM(SIZE), 0) FROM DETAILS "
                        "WHERE MIME LIKE ? AND PATH LIKE ? ESCAPE '\\'",
                        (kind + "/%", _like_prefix(base)),
                    ).fetchone()
                    if count:
                        stat.kinds[kind] = (count, size)
                stats.folders.append(stat)

            if folders:
                clauses = " AND ".join("PATH NOT LIKE ? ESCAPE '\\'" for _ in folders)
                stats.unmatched = con.execute(
                    f"SELECT COUNT(*) FROM DETAILS WHERE MIME IS NOT NULL AND {clauses}",
                    [_like_prefix(p.rstrip("/")) for p in folders],
                ).fetchone()[0]

        stats.available = True
    except sqlite3.DatabaseError as exc:
        stats.error = str(exc)
    except OSError as exc:
        stats.error = str(exc)

    return stats


def _like_prefix(base: str) -> str:
    """A LIKE pattern matching everything below ``base``."""
    for char in ("\\", "%", "_"):
        base = base.replace(char, "\\" + char)
    return base + "/%"


def search_items(
    db_path: str,
    query: str = "",
    kind: str = "",
    folder: str = "",
    order: str = "recent",
    limit: int = 400,
) -> Tuple[List[MediaItem], int]:
    """Return matching indexed files and the total number of matches.

    The result is capped at ``limit`` rows so that a library of any size stays
    responsive; the count reports how many there really are.
    """
    if not os.path.isfile(db_path):
        return [], 0

    where = ["MIME IS NOT NULL"]
    params: list = []

    if kind:
        where.append("MIME LIKE ?")
        params.append(kind + "/%")
    if folder:
        where.append("PATH LIKE ? ESCAPE '\\'")
        params.append(_like_prefix(folder.rstrip("/")))
    if query:
        where.append("(TITLE LIKE ? ESCAPE '\\' OR PATH LIKE ? ESCAPE '\\')")
        params.extend([_like(query), _like(query)])

    clause = " AND ".join(where)
    order_sql = {
        "recent": "TIMESTAMP DESC, TITLE COLLATE NOCASE",
        "name": "TITLE COLLATE NOCASE",
        "size": "SIZE DESC",
    }.get(order, "TIMESTAMP DESC")

    try:
        with _connect(db_path) as con:
            total = con.execute(f"SELECT COUNT(*) FROM DETAILS WHERE {clause}", params).fetchone()[0]
            rows = con.execute(
                "SELECT TITLE, PATH, SIZE, DURATION, RESOLUTION, MIME, TIMESTAMP "
                f"FROM DETAILS WHERE {clause} ORDER BY {order_sql} LIMIT ?",
                params + [limit],
            ).fetchall()
    except (sqlite3.DatabaseError, OSError):
        return [], 0

    items = [
        MediaItem(
            title=row[0] or os.path.basename(row[1] or ""),
            path=row[1] or "",
            size=row[2] or 0,
            duration=row[3] or "",
            resolution=row[4] or "",
            mime=row[5] or "",
            timestamp=row[6] or 0,
        )
        for row in rows
    ]
    return items, total


# ----------------------------------------------------------------- formatting


def format_size(size: int) -> str:
    """Human readable byte count, in binary units."""
    if size <= 0:
        return "0 B"
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    value = float(size)
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    if index == 0:
        return f"{int(value)} {units[index]}"
    return f"{value:.1f} {units[index]}"


def format_duration(duration: str) -> str:
    """Turn minidlna's ``H:MM:SS.mmm`` into something compact."""
    if not duration:
        return ""
    head = duration.split(".", 1)[0]
    parts = head.split(":")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return head
    while len(numbers) < 3:
        numbers.insert(0, 0)
    hours, minutes, seconds = numbers[-3:]
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def format_count(count: int, noun: str) -> str:
    """``1 file`` / ``3 files``."""
    return f"{count:,} {noun}" + ("" if count == 1 else "s")
